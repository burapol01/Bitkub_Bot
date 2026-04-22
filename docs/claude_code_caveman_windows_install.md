# Claude Code + Caveman on Windows (VS Code)

เอกสารนี้สรุปวิธีติดตั้ง `caveman` สำหรับ Claude Code บน Windows ผ่าน VS Code และสรุปปัญหาที่เจอจริงระหว่างติดตั้ง พร้อมวิธีแก้

## สภาพแวดล้อมที่ใช้

- OS: Windows PowerShell
- IDE: VS Code
- Claude Code extension: ติดตั้งผ่าน VS Code extension
- Local marketplace/plugin repo: `D:\Projects\caveman`
- Project ที่ใช้งานจริง: `D:\Projects\Bitkub_Bot`

## อาการที่เจอ

### 1. `claude` command ไม่ถูกพบ

อาการ:

```powershell
claude : The term 'claude' is not recognized ...
```

สาเหตุ:

- `claude.exe` มาพร้อม VS Code extension
- แต่ยังไม่ได้อยู่ใน `PATH` ของ PowerShell session ปัจจุบัน

### 2. Claude Code บอกว่า Windows ต้องใช้ Git Bash

อาการ:

```powershell
Claude Code on Windows requires git-bash ...
```

สาเหตุ:

- Claude Code บน Windows ใช้ Git Bash ภายใน
- เครื่องมี `git.exe` แล้ว แต่ Claude ยังไม่รู้ path ของ `bash.exe`

### 3. `/caveman:compress` ใน VS Code panel ไม่ขึ้น

สาเหตุ:

- VS Code extension รองรับ command/skill แค่บางส่วน
- บางงานควรใช้ Claude CLI ใน integrated terminal แทน

### 4. `caveman-compress` เรียกไม่ผ่านด้วย Python script

อาการ:

```powershell
❌ Error: [WinError 2] The system cannot find the file specified
```

สาเหตุ:

- script `caveman-compress` พยายาม fallback ไปหา `claude` CLI
- ถ้า `claude` ยังไม่อยู่ใน `PATH` ก็จะเรียกไม่เจอ

## สิ่งที่ตรวจพบจริง

### `claude.exe` อยู่ตรงไหน

บนเครื่องนี้ `claude.exe` อยู่ใน VS Code extension path:

```text
C:\Users\soontonsin.pue\.vscode\extensions\anthropic.claude-code-2.1.117-win32-x64\resources\native-binary\claude.exe
```

### `bash.exe` อยู่ตรงไหน

บนเครื่องนี้ `bash.exe` อยู่ที่:

```text
C:\Users\soontonsin.pue\AppData\Local\Programs\Git\bin\bash.exe
```

## วิธีติดตั้งที่ใช้งานได้จริง

### 1. หา `claude.exe` จาก VS Code extension

รันใน PowerShell:

```powershell
$ClaudeExe = Get-ChildItem "$env:USERPROFILE\.vscode\extensions\anthropic.claude-code-*-win32-x64\resources\native-binary\claude.exe" -ErrorAction Stop |
  Sort-Object FullName -Descending |
  Select-Object -First 1 -ExpandProperty FullName
```

ทดสอบ:

```powershell
& $ClaudeExe --version
```

ถ้าสำเร็จจะขึ้นประมาณ:

```text
2.1.117 (Claude Code)
```

### 2. ตั้งค่า Git Bash path ให้ Claude Code

แบบชั่วคราวใน shell นี้:

```powershell
$env:CLAUDE_CODE_GIT_BASH_PATH='C:\Users\soontonsin.pue\AppData\Local\Programs\Git\bin\bash.exe'
```

### 3. เพิ่ม marketplace จาก local repo

```powershell
& $ClaudeExe plugin marketplace add 'D:\Projects\caveman'
```

ผลที่สำเร็จ:

```text
✔ Successfully added marketplace: caveman (declared in user settings)
```

### 4. ติดตั้ง plugin

```powershell
& $ClaudeExe plugin install caveman@caveman
```

ผลที่สำเร็จ:

```text
✔ Successfully installed plugin: caveman@caveman (scope: user)
```

## ทำให้ใช้ได้ถาวร

เพิ่ม `CLAUDE_CODE_GIT_BASH_PATH` ลง `~/.claude/settings.json`

ไฟล์:

```text
C:\Users\soontonsin.pue\.claude\settings.json
```

ตัวอย่าง:

```json
{
  "effortLevel": "high",
  "env": {
    "CLAUDE_CODE_GIT_BASH_PATH": "C:\\Users\\soontonsin.pue\\AppData\\Local\\Programs\\Git\\bin\\bash.exe"
  }
}
```

หมายเหตุ:

- ถ้ามี key อื่นอยู่แล้ว ให้ merge เข้าไฟล์เดิม
- หลังแก้แล้วเปิด Claude/terminal ใหม่

## วิธีใช้งานหลังติดตั้ง

ใน Claude Code ใช้ได้เช่น:

```text
/caveman
/caveman ultra
/caveman-help
/caveman:compress README.md
stop caveman
```

## วิธีใช้ `caveman-compress`

### ใช้ผ่าน Claude Code โดยตรง

ถ้า command โผล่ใน UI:

```text
/caveman:compress README.md
```

### ใช้ผ่าน Python script

ถ้าต้องการรันตรง:

```powershell
Set-Location 'D:\Projects\Bitkub_Bot\.agents\skills\caveman-compress'
python -m scripts 'D:\Projects\Bitkub_Bot\README.md'
```

ข้อสำคัญ:

- ต้องรันจากโฟลเดอร์ `caveman-compress` ก่อน
- ต้องชี้ไปที่ไฟล์ที่มีอยู่จริง
- script จะเขียนทับไฟล์เดิม
- และสร้าง backup เป็น `*.original.md`

ตัวอย่างผลลัพธ์:

```text
Compression completed successfully
Compressed: D:\Projects\Bitkub_Bot\README.md
Original:   D:\Projects\Bitkub_Bot\README.original.md
```

## เรื่อง billing / API key

กรณีนี้ไม่จำเป็นต้องใช้ `ANTHROPIC_API_KEY`

เหตุผล:

- `caveman-compress` สามารถ fallback ไป `claude` CLI ได้
- เมื่อ `claude` CLI ใช้ auth จาก Claude Code / VS Code ที่ล็อกอินอยู่แล้ว ก็ไม่ต้องพึ่ง API key แยก

ดังนั้น:

- ถ้าใช้ Claude Code subscription ผ่าน VS Code อยู่แล้ว ไม่ควรตั้ง `ANTHROPIC_API_KEY` โดยไม่จำเป็น
- เพราะอาจทำให้ flow บางส่วนไปใช้ API billing แทน

## Troubleshooting สั้น ๆ

### `claude` ไม่ถูกพบ

ให้รันผ่าน `$ClaudeExe` ก่อน หรือเพิ่ม path ของ `claude.exe` เข้า `PATH`

### ขึ้น `requires git-bash`

ให้ตั้ง:

```powershell
$env:CLAUDE_CODE_GIT_BASH_PATH='C:\Users\soontonsin.pue\AppData\Local\Programs\Git\bin\bash.exe'
```

หรือใส่ใน `~/.claude/settings.json`

### ใช้ `Set-Location` ไม่ได้

มักเกิดจากเอา prompt shell ไป paste ทั้งบรรทัด เช่น:

```text
PS C:\Users\...> Set-Location ...
```

ให้ paste เฉพาะคำสั่ง:

```powershell
Set-Location 'D:\Projects\Bitkub_Bot\.agents\skills\caveman-compress'
```

### `No module named scripts`

เกิดเมื่อรัน `python -m scripts ...` จากโฟลเดอร์ผิด

ให้เข้าโฟลเดอร์นี้ก่อน:

```powershell
Set-Location 'D:\Projects\Bitkub_Bot\.agents\skills\caveman-compress'
```

### บีบไฟล์ไม่สำเร็จเพราะไฟล์ไม่มี

ตรวจด้วย:

```powershell
Test-Path 'D:\Projects\Bitkub_Bot\README.md'
Test-Path 'D:\Projects\Bitkub_Bot\CLAUDE.md'
```

## คำสั่งสั้นชุดเดียวที่ใช้งานได้

```powershell
$ClaudeExe = Get-ChildItem "$env:USERPROFILE\.vscode\extensions\anthropic.claude-code-*-win32-x64\resources\native-binary\claude.exe" -ErrorAction Stop |
  Sort-Object FullName -Descending |
  Select-Object -First 1 -ExpandProperty FullName

$env:CLAUDE_CODE_GIT_BASH_PATH='C:\Users\soontonsin.pue\AppData\Local\Programs\Git\bin\bash.exe'

& $ClaudeExe --version
& $ClaudeExe plugin marketplace add 'D:\Projects\caveman'
& $ClaudeExe plugin install caveman@caveman
```

## สรุป

ปัญหาหลักมี 2 ชั้น:

1. PowerShell หา `claude` ไม่เจอ
2. Claude Code หา `bash.exe` ไม่เจอ

หลังแก้ 2 จุดนี้แล้ว:

- add marketplace สำเร็จ
- install plugin สำเร็จ
- `caveman` และ `caveman-compress` ใช้งานต่อได้
