#!/bin/zsh
# 編譯 wpkit。Info.plist 必須嵌入執行檔,否則 EventKit 會因為缺少用途說明而被拒絕。
set -euo pipefail

here=${0:A:h}
out=$here/../bin
mkdir -p "$out"

swiftc -O "$here/wpkit.swift" -o "$out/wpkit" \
  -framework EventKit \
  -Xlinker -sectcreate -Xlinker __TEXT -Xlinker __info_plist -Xlinker "$here/Info.plist"

codesign -s - -f --identifier com.workplan.wpkit "$out/wpkit"
print "已編譯 $out/wpkit"
