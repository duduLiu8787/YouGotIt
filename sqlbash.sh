#!/bin/bash

TARGETFILE="targets.txt"
OUTPUTDIR="sqlmap_results"
VULNERABLE_LIST="vulnerable_targets.txt"

mkdir -p "$OUTPUTDIR"
> "$VULNERABLE_LIST"  # 清空舊的

while IFS= read -r line; do
    echo "[*] Scanning: $line"
    OUTFILE="$OUTPUTDIR/$(echo "$line" | sed 's|https\?://||; s|/|_|g').txt"
    
    sqlmap -u "$line" --crawl=2 --forms --batch \
        --random-agent --threads=5 --level=2 --risk=1 \
        > "$OUTFILE" 2>&1
    
    # sqlmap發現漏洞時output會有這些字串
    if grep -qE "(is vulnerable|Parameter:|sqlmap identified)" "$OUTFILE"; then
        echo "[!!!] VULNERABLE: $line"
        echo "$line" >> "$VULNERABLE_LIST"
        
        # 順便把發現的參數也記錄下來
        echo "=== $line ===" >> "$VULNERABLE_LIST"
        grep -E "(Parameter:|Type:|Title:)" "$OUTFILE" >> "$VULNERABLE_LIST"
        echo "" >> "$VULNERABLE_LIST"
    else
        echo "[-] Clean: $line"
    fi

done < "$TARGETFILE"

echo ""
echo "===== SUMMARY ====="
echo "Vulnerable targets saved to: $VULNERABLE_LIST"
cat "$VULNERABLE_LIST"
