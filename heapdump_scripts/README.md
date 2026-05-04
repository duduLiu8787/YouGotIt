# hprof 分析工具集

從 Java heap dump(`.hprof`)抽資料的純 Python 工具,**無外部依賴**(只用 stdlib)。
專為 MAT 做不到的事情設計:**byte[] 內容掃描、HTTP buffer 復原、客製化 keyword 搜尋**。

---

## 內容

| 檔案 | 用途 |
|---|---|
| **`hprof_walker.py`** | 共用 library — 解析 hprof binary,callback-based |
| `extract_cases.py` | 抽 SOAP `<jsonString>...` 病例(專案特化) |
| `extract_http_responses.py` | 找 cached HTTP request/response,gunzip body |
| `find_keyword.py` | 通用 keyword 搜尋(byte[] + char[]) |
| `dump_strings.py` | 把所有 String 內容 dump 成文字檔 |
| `dump_classes.py` | 把所有已載入 class 名 dump 成清單 |
| `histogram.py` | 每個 class 的 instance 數量統計 |

---

## 為什麼用這些(而不是 MAT)

| 場景 | MAT | 這套 |
|---|---|---|
| 找 java.lang.String 內含字串 | ✅ OQL 強 | ⚠️ 慢 |
| 找 byte[] 內含字串 | ❌ 不會 decode | ✅ 直接掃 |
| 復原 HTTP buffer / SOAP body | ❌ 沒這 query | ✅ 內建 |
| Class histogram | ✅ 內建 | ✅ |
| Object graph traversal | ✅ 強 | ❌ 不做 |
| Dominator tree / leak 分析 | ✅ 強 | ❌ 不做 |
| 自訂 regex 搜尋 | ⚠️ OQL `LIKE` 限 String | ✅ Python regex 任何地方 |

→ **互補使用**:結構分析 / 物件追蹤用 MAT,內容掃描 / 大量 byte[] decode 用這套。

---

## 安裝

把整個 `scripts/` 目錄複製到任何地方。需要 **Python 3.7+**(stdlib only)。

```bash
# 例如
mkdir hprof-tools
cd hprof-tools
# 把 7 個 .py 檔放這裡
```

---

## 使用方法

所有腳本第一個參數都是 `.hprof` 檔路徑,第二個(可選)是輸出檔。

### 1. `extract_cases.py` — 抽 SOAP 病例 JSON + lab 殘片

從 byte[] buffer 找 case 資料,使用 **5 個策略**,結果按 buffer 分組:

| Strategy | 抓什麼 |
|---|---|
| **1** | 完整 `<jsonString>{...}</jsonString>` |
| **2** | 開頭有 `<jsonString>{`,結尾被截掉 → forward brace-balance |
| **3** | 結尾有 `}</jsonString>`,開頭被截掉 → backward brace-balance |
| **4** | 內嵌 lab object:含 `AntibodyId`/`PathogenId` 的 `{...}` 子物件(從被截掉的 envelope 中救出) |
| **5** | Loose field fragments:獨立的 `"ResultDay":"..."`、`"IDNO":"..."` 等(連 `{}` 都沒了的殘片) |

```bash
# 完整(預設,strategies 1-5)
python extract_cases.py heap.hprof cases.txt

# 只要完整 envelope(strategies 1-3,跳過 fragments)
python extract_cases.py heap.hprof cases.txt --no-fragments
```

**輸出範例**(精簡):
```
======================================================================
## Buffer 1: byte[] @ 0x88c64b50
======================================================================

--- Envelope 1 ---
{"ReportId":"1153111775855","DiseaseId":"010","DetermineStatus":"31",...}

--- Inner lab objects (1) ---
  [Lab 1] @offset=1474:
  {"SeqNo":1,"PathogenName":"螢光定量聚合酶連鎖反應(real-time PCR)",
   "PathogenResultName":"陰性","ResultDay":"2026-02-13 16:39:36"}

--- Loose field fragments (1) ---
  ResultDay = "2026-04-20 14:15:33"
    context: ...PathogenResultName":"檢體保留","ResultDay":"2026-04-20 14:15:33"...
```

**為什麼要 fragment**:Tomcat 重用 byte[] buffer。每次新 SOAP request 寫入只蓋過開頭,**舊 request body 殘片留在後段**。Fragment 抽取把這些救出來,通常能找到比 envelope 多 2-4 倍的 case 資料。

**自訂用途**:改 `extract_cases.py` 內的:
- `JSON_ANCHOR` — envelope 必須含的 keyword(預設 `'ReportId'`)
- `LAB_ANCHORS` — 內嵌物件的 anchor list(預設 `['AntibodyId', 'PathogenId', ...]`)
- `LOOSE_FIELDS` — loose 抓的欄位名 list(預設含 `ResultDay`, `IDNO`, `CHART_NO` 等)

### 2. `extract_http_responses.py` — 復原 HTTP request/response

```bash
python extract_http_responses.py heap.hprof
python extract_http_responses.py heap.hprof http.txt
```

找含 `HTTP/1.1` / `GET /` / `Set-Cookie` / `Content-Encoding: gzip` 的 byte[],印 header + body(gzip 自動解壓)。

**用得到的時候**:
- 找出最近處理過的 request URI / cookie / source IP
- 看 cached response 的 JSON body(例如 Spring Boot Actuator 回應)
- 攻擊取證

### 3. `find_keyword.py` — 通用 keyword 搜尋

```bash
# 單一 keyword
python find_keyword.py heap.hprof password

# 多 keyword
python find_keyword.py heap.hprof ReportId IDNO CHART_NO -o phi.txt

# 中文(直接用 UTF-8)
python find_keyword.py heap.hprof "鉤端螺旋體病" "確定病例" -o medical.txt
```

每個 keyword 在 byte[] (UTF-8) 跟 char[] (UTF-16) 都搜。每個命中印**前後 context**(預設 100 字)。

#### Flags

| Flag | 預設 | 說明 |
|---|---|---|
| `-o <file>` | stdout | 寫到檔(hits 多時建議用) |
| `--dedup` | off | 去重相同 snippet(預設**全部顯示**不去重) |
| `--max-per-array N` | 100 | 單一 byte[]/char[] 內最多報幾個 hit(避免同一個 buffer 灌爆輸出) |
| `--context N` | 100 | 命中位置前後顯示幾個 byte/char |

#### 輸出統計

每次跑會在輸出開頭印:
```
# Total raw hits: 82
# After --dedup:  56     ← 只在 --dedup 時出現
# Source: heap.hprof
```

#### 典型用途

| 場景 | 範例 |
|---|---|
| 找密碼 / token | `python find_keyword.py heap.hprof "password" "secret" "token"` |
| 找特定病例 ID | `python find_keyword.py heap.hprof "1153111775855" "IDNO"` |
| 找 SQL fragment | `python find_keyword.py heap.hprof "SELECT" --max-per-array 30` |
| 找錯誤訊息 | `python find_keyword.py heap.hprof "Exception" --dedup` |
| 看完整前後文 | `python find_keyword.py heap.hprof "IDNO" --context 300` |

#### 不去重 vs 去重的選擇

- **預設(不去重)**:看到所有 raw hit,確認沒漏。例如同一個 keyword 在多個 byte[] 出現,每個都顯示
- **`--dedup`**:同一段 snippet 文字只顯示一次,適合快速概覽
- **建議流程**:先 `--dedup` 看大概,再不加 flag 跑一次到檔案做完整 audit

### 4. `dump_strings.py` — 全部字串

```bash
python dump_strings.py heap.hprof strings.txt
```

去重 + 排序所有 char[](Java 8 String 背後)內容。**通常 1-5 MB,grep-able**。

之後可以:
```bash
grep -i "password" strings.txt
grep -E "^https?://" strings.txt
grep -E "^[A-Z][12][0-9]{8}$" strings.txt    # 找台灣身分證格式
```

### 5. `dump_classes.py` — 全部 class 名

```bash
python dump_classes.py heap.hprof classes.txt
```

去重 + 排序所有已載入 class 名(包含 CGLIB proxy / 反射代理)。

```bash
grep "^com\." classes.txt          # 看應用代碼
grep "Entity$" classes.txt         # 找 JPA entity
grep "Controller$" classes.txt     # 找 Spring controllers
grep -i "auth" classes.txt         # 找 auth 相關
```

### 6. `histogram.py` — instance 數量統計

```bash
python histogram.py heap.hprof histogram.txt
```

跟 MAT histogram 一樣,但純文字 + sort by count。看哪個 class 物件最多。

```bash
head -30 histogram.txt   # top 30
```

---

## 直接用 library 寫客製化

```python
from hprof_walker import Walker

w = Walker('heap.hprof')

# 任何 byte[] 內容含 "secret" → 印出來
def on_byte(aid, raw):
    if b'secret' in raw:
        print(f'0x{aid:x}: {raw[:200]}')

w.on_byte_array = on_byte
w.run()
```

完整 callback 列表:

| Callback | 簽名 | 說明 |
|---|---|---|
| `on_utf8(sid, text)` | (int, str) | UTF8 record(class/field/method 名) |
| `on_class(cid, name)` | (int, str) | LOAD_CLASS,name 已轉 dot 格式 |
| `on_byte_array(aid, raw)` | (int, bytes) | byte[] 原始 bytes |
| `on_char_array(aid, text)` | (int, str) | char[] 已 utf-16be decode |
| `on_int_array(aid, raw)` | (int, bytes) | int[] 原始 bytes |
| `on_other_prim_array(aid, type_tag, raw)` | (int, int, bytes) | short/long/float/... |
| `on_instance(oid, cid, raw)` | (int, int, bytes) | 物件實例 + raw field bytes |
| `on_obj_array(aid, cid, ids)` | (int, int, list[int]) | Object[] 元素 id 列表 |
| `on_progress(record_count)` | (int,) | 每 100k records 觸發 |

走完後:
```python
w.all_classes()      # dict {cid: name}
w.class_name(cid)    # 查特定 cid 的名字
```

---

## 範例 recipes

### a. 找所有含 `@gmail.com` 的字串

```bash
python find_keyword.py heap.hprof "@gmail.com" --dedup -o emails.txt
```

### b. 找所有 IP 地址

```bash
python dump_strings.py heap.hprof strings.txt
grep -E '^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}$' strings.txt | sort -u
```

### c. 列出所有 Spring Controller

```bash
python dump_classes.py heap.hprof classes.txt
grep -E '(Controller|RestController)$' classes.txt
```

### d. 找出最大記憶體 class top 30

```bash
python histogram.py heap.hprof histogram.txt
head -32 histogram.txt
```

### e. 客製化:找所有 `password` 出現位置(byte[] + char[])

```python
# my_search.py
from hprof_walker import Walker

w = Walker('heap.hprof')

def on_byte(aid, raw):
    pos = raw.lower().find(b'password')
    while pos >= 0:
        print(f'byte[]@0x{aid:x}: ...{raw[max(0,pos-50):pos+50]}...')
        pos = raw.lower().find(b'password', pos+1)

def on_char(aid, text):
    pos = text.lower().find('password')
    while pos >= 0:
        print(f'char[]@0x{aid:x}: ...{text[max(0,pos-50):pos+50]}...')
        pos = text.lower().find('password', pos+1)

w.on_byte_array = on_byte
w.on_char_array = on_char
w.run()
```

```bash
python my_search.py > pwd_hits.txt
```

---

## 限制 / 已知問題

1. **不做 graph traversal** — 找不到「誰持有這個物件」。要追物件關係用 MAT。
2. **byte[] decode 用 UTF-8** — 不是 UTF-8 的二進位資料(序列化、加密等)會出現亂碼。
3. **char[] 用 UTF-16BE** — Java 8 標準。Java 9+ 有 compact string(byte[] Latin-1),這套不會自動偵測。
4. **不解壓 deflate**(只 gzip)— 如果 server 用 deflate encoding 而非 gzip,需要自己加 `zlib.decompress()`。
5. **String 內容找不到時**:Java 8 中 String 是 char[],已處理;Java 9+ 是 byte[],要把 `extract_cases.py` 第三策略移植到 char[] 並偵測 compact string。

---

## 跟 hprof 規格的對應

如果想擴充 / debug,參考 [hprof binary format spec](https://hg.openjdk.java.net/jdk6/jdk6/jdk/raw-file/tip/src/share/demo/jvmti/hprof/manual.html)。

`hprof_walker.py` 內已標註所有 tag 值。新版本 hprof 可能多新 tag,如果遇到 `WARN: unknown heap dump sub-tag 0xXX`,在 walker 加對應分支即可。

---

## 授權

Public domain — 隨便拿去改。
