# วิธี Generate SSH Key ใหม่สำหรับ GitHub Actions Deploy ไป DigitalOcean

เอกสารนี้สรุปขั้นตอนแบบปลอดภัย สำหรับการสร้าง SSH key ใหม่ แล้วนำไปใช้กับ GitHub Actions และ server

---

## เป้าหมาย

เราจะทำ 3 อย่าง

1. สร้าง **SSH key คู่ใหม่**
2. เอา **private key** ไปใส่ใน GitHub Secret
3. เอา **public key** ไปเพิ่มใน server ที่ไฟล์ `authorized_keys`

---

## ไฟล์ที่ได้คืออะไร

ถ้าสร้าง key ชื่อ `github_actions_do_new` จะได้ 2 ไฟล์

- `github_actions_do_new` = **private key**
- `github_actions_do_new.pub` = **public key**

> หมายเหตุ: ไฟล์ `.pub` ไม่ใช่ Microsoft Publisher จริง ๆ  
> Windows แค่แสดง type แบบนั้นเฉย ๆ

---

## แนะนำวิธีที่ปลอดภัย

แนะนำให้ **สร้าง key ใหม่คนละชื่อก่อน**  
อย่าทับของเดิมทันที

เหตุผล:
- ถ้ายังตั้งค่า GitHub หรือ server ไม่ครบ ของเดิมยัง deploy ได้
- เมื่อทดสอบผ่านแล้ว ค่อยลบ key เก่าทีหลัง

---

## ขั้นตอนที่ 1: สร้าง SSH key ใหม่บน Windows

เปิด **PowerShell** แล้วรัน:

```powershell
ssh-keygen -t ed25519 -C "github-actions-deploy" -f $env:USERPROFILE\.ssh\github_actions_do_new
```

ระบบอาจถาม passphrase

- ถ้าต้องการตั้ง ก็ใส่ได้
- ถ้าใช้กับ GitHub Actions โดยตรง มักนิยมกด Enter ผ่านให้ว่าง

หลังจากรันเสร็จ จะได้ไฟล์:

```text
C:\Users\<ชื่อคุณ>\.ssh\github_actions_do_new
C:\Users\<ชื่อคุณ>\.ssh\github_actions_do_new.pub
```

---

## ขั้นตอนที่ 2: เปิดดู private key

ใช้คำสั่งนี้เพื่อคัดลอก private key ทั้งก้อน:

```powershell
Get-Content $env:USERPROFILE\.ssh\github_actions_do_new -Raw
```

จะได้ข้อความประมาณนี้:

```text
-----BEGIN OPENSSH PRIVATE KEY-----
...
-----END OPENSSH PRIVATE KEY-----
```

ให้ copy **ทั้งหมด** รวมบรรทัด BEGIN/END

---

## ขั้นตอนที่ 3: ใส่ private key ใน GitHub Secret

เข้า GitHub repo ของคุณ แล้วไปที่:

- **Settings**
- **Secrets and variables**
- **Actions**

จากนั้น:

1. กด **New repository secret**
2. ตั้งชื่อว่า:

```text
DO_SSH_PRIVATE_KEY
```

3. วางค่า private key ที่ copy มา
4. กด Save

> ชื่อ secret ต้องตรงเป๊ะกับที่ workflow ใช้

---

## ขั้นตอนที่ 4: เปิดดู public key

ใช้คำสั่งนี้:

```powershell
Get-Content $env:USERPROFILE\.ssh\github_actions_do_new.pub
```

จะได้ข้อความประมาณนี้:

```text
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI.... github-actions-deploy
```

ให้ copy ทั้งบรรทัด

---

## ขั้นตอนที่ 5: เพิ่ม public key ลง server

SSH เข้า server แล้วรัน:

```bash
mkdir -p ~/.ssh
chmod 700 ~/.ssh
nano ~/.ssh/authorized_keys
```

จากนั้น:
- วาง public key ที่ copy มา **เพิ่มลงไป**
- อย่าลบ key เดิมทิ้งทันที ถ้ายังไม่ได้ทดสอบ
- save file

แล้วรันต่อ:

```bash
chmod 600 ~/.ssh/authorized_keys
```

---

## ขั้นตอนที่ 6: ทดสอบ deploy

เมื่อใส่ครบแล้ว ให้:

1. push code ขึ้น GitHub
2. รอ GitHub Actions ทำงาน
3. เช็กว่า workflow ผ่าน
4. เช็กที่ server ว่า service ยังขึ้นปกติ

คำสั่งเช็กบน server:

```bash
cd /opt/bitkub/Bitkub_Bot
git log --oneline -n 3
systemctl is-active bitkub-engine bitkub-streamlit
```

ถ้าขึ้นแบบนี้ถือว่าโอเค:

```text
active
active
```

---

## ถ้าจะเปลี่ยนชื่อไฟล์ให้เหมือนของเดิม

ถ้าคุณอยากใช้ชื่อเดิม เช่น `github_actions_do` ก็ทำได้:

```powershell
ssh-keygen -t ed25519 -C "github-actions-deploy" -f $env:USERPROFILE\.ssh\github_actions_do
```

แต่ถ้ามีไฟล์เดิมอยู่แล้ว ระบบจะถามก่อนว่าจะ overwrite ไหม

> แนะนำให้ใช้ชื่อใหม่ก่อน เช่น `github_actions_do_new`  
> จะปลอดภัยกว่า

---

## known_hosts คืออะไร

ไฟล์ `known_hosts` **ไม่ใช่ private/public key**

มันใช้เก็บลายนิ้วมือของ server เพื่อยืนยันว่าเรากำลังเชื่อมต่อกับเครื่องเดิม

ปกติ:
- ไม่ต้อง generate ใหม่ทุกครั้ง
- จะเปลี่ยนเมื่อ server เปลี่ยน
- หรือ host key เปลี่ยน
- หรือ IP/เครื่องใหม่

ถ้าจะเติมค่าใหม่ให้ `known_hosts`:

```powershell
ssh-keyscan -H 165.22.108.218 >> $env:USERPROFILE\.ssh\known_hosts
```

---

## วิธีเช็กว่าไฟล์ไหนคืออะไร

โดยทั่วไป:

- ไม่มี `.pub` = private key
- มี `.pub` = public key

ตัวอย่างของคุณ:

- `github_actions_do` = private key
- `github_actions_do.pub` = public key
- `known_hosts` = รายชื่อ host ที่เคยเชื่อมต่อ

---

## ถ้าจะหมุน key ใหม่ในอนาคต

แนวทางที่ดีคือ:

1. สร้าง key ใหม่คนละชื่อ
2. เพิ่ม public key เข้า server ก่อน
3. เปลี่ยน GitHub Secret ให้ใช้ private key ใหม่
4. ทดสอบ deploy
5. ถ้าผ่านแล้วค่อยลบ key เก่าออกจาก server และเครื่อง local

วิธีนี้จะเสี่ยงน้อยที่สุด

---

## สรุปสั้น ๆ

- **ได้** สามารถ generate SSH key ใหม่ได้
- ให้เอา **private key** ไปใส่ GitHub Secret `DO_SSH_PRIVATE_KEY`
- ให้เอา **public key** ไปเพิ่มใน `~/.ssh/authorized_keys` ของ server
- `known_hosts` ไม่ใช่ key คู่ และไม่ต้อง generate แบบเดียวกัน
- แนะนำให้สร้างใหม่เป็น **คนละชื่อก่อน** เพื่อความปลอดภัย

---

## คำสั่งที่ใช้บ่อย

### สร้าง key ใหม่
```powershell
ssh-keygen -t ed25519 -C "github-actions-deploy" -f $env:USERPROFILE\.ssh\github_actions_do_new
```

### ดู private key
```powershell
Get-Content $env:USERPROFILE\.ssh\github_actions_do_new -Raw
```

### ดู public key
```powershell
Get-Content $env:USERPROFILE\.ssh\github_actions_do_new.pub
```

### เพิ่ม host ลง known_hosts
```powershell
ssh-keyscan -H 165.22.108.218 >> $env:USERPROFILE\.ssh\known_hosts
```

### เพิ่ม public key ฝั่ง server
```bash
mkdir -p ~/.ssh
chmod 700 ~/.ssh
nano ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys
```

