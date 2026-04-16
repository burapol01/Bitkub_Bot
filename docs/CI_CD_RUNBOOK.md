# CI/CD Runbook

เอกสารนี้สรุป flow ที่ใช้อยู่ตอนนี้สำหรับโปรเจ็กต์ `Bitkub_Bot` บน GitHub + DigitalOcean แบบอ่านไฟล์เดียวแล้วทำตามได้เลย

## เป้าหมาย

เราใช้ flow นี้:

1. ทำงานบน branch
2. เปิด PR เข้า `main`
3. ให้ GitHub Actions รัน `CI`
4. merge เข้า `main` เมื่อ `CI` ผ่าน
5. ให้ GitHub Actions รัน `CD` เพื่อ deploy ไปที่ DigitalOcean

แนวทางนี้ปลอดภัยกว่า push ตรงเข้า `main` เพราะช่วยกัน syntax error, config error, และ workflow พังก่อน deploy จริง

## ไฟล์สำคัญ

- `.github/workflows/ci.yml`
- `.github/workflows/deploy.yml`
- `deploy/deploy_prod.sh`
- `deploy/DEPLOY_SECRETS_CHECKLIST.md`
- `deploy/BRANCH_PROTECTION_CHECKLIST.md`
- `docs/generate_new_ssh_key_github_actions.md`

## ภาพรวมของระบบ

### CI ทำอะไร

workflow `CI` จะรันเมื่อ:

- เปิดหรืออัปเดต PR เข้า `main`
- push เข้า `main`
- กด `workflow_dispatch` เอง

สิ่งที่ CI ตรวจ:

- install dependencies จาก `requirements.txt`
- validate config ด้วย `reload_config()`
- compile ไฟล์ `.py` ที่ tracked ทั้ง repo
- ตรวจว่า version metadata อ่านได้

### CD ทำอะไร

workflow `Deploy Production` จะรันเมื่อ:

- `CI` ที่มาจาก `push` บน `main` สำเร็จ
- หรือกด `workflow_dispatch` เอง

สิ่งที่ CD ทำ:

1. SSH เข้า DigitalOcean droplet
2. เรียก `deploy/deploy_prod.sh`
3. ให้ server `git fetch` และ `git pull --ff-only`
4. install dependencies
5. รัน smoke checks
6. เขียน deploy version metadata
7. restart `bitkub-engine` และ `bitkub-streamlit`

## One-Time Setup

### 1. สร้าง SSH key สำหรับ GitHub Actions

ดูรายละเอียดเต็มได้ที่ [generate_new_ssh_key_github_actions.md](/d:/Projects/Bitkub_Bot/docs/generate_new_ssh_key_github_actions.md)

คำสั่งตัวอย่างบน PowerShell:

```powershell
ssh-keygen -t ed25519 -C "github-actions-deploy" -f $env:USERPROFILE\.ssh\github_actions_do_new
```

ไฟล์ที่จะได้:

- private key: `C:\Users\<user>\.ssh\github_actions_do_new`
- public key: `C:\Users\<user>\.ssh\github_actions_do_new.pub`

### 2. เอา public key ไปใส่ที่ server

บน droplet:

```bash
mkdir -p ~/.ssh
chmod 700 ~/.ssh
nano ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys
```

วาง public key เพิ่มเข้าไป อย่าลบ key เดิมจนกว่าจะทดสอบ key ใหม่ผ่าน

### 3. ตั้ง GitHub Secrets

ดูรายละเอียดเต็มได้ที่ [DEPLOY_SECRETS_CHECKLIST.md](/d:/Projects/Bitkub_Bot/deploy/DEPLOY_SECRETS_CHECKLIST.md)

ต้องมี secrets อย่างน้อย:

- `DO_SSH_HOST`
- `DO_SSH_PORT`
- `DO_SSH_USER`
- `DO_SSH_PRIVATE_KEY`
- `DO_SSH_KNOWN_HOSTS`

ตัวอย่าง `known_hosts`:

```bash
ssh-keyscan -H YOUR_DROPLET_IP
```

### 4. ให้ server pull repo ได้เอง

บน droplet ต้องรันคำสั่งนี้ผ่าน:

```bash
cd /opt/bitkub/Bitkub_Bot
git fetch origin main
```

ถ้า repo เป็น private ให้เตรียม deploy key หรือ token ฝั่ง server ให้พร้อม

### 5. ให้ deploy user เข้าถึง Docker ได้

ถ้า deploy user ยังไม่อยู่ใน `docker` group ให้เพิ่มครั้งเดียวบน server:

```bash
sudo usermod -aG docker bitkub
```

แล้ว log out / log in ใหม่เพื่อให้ group membership มีผล

ทดสอบ:

```bash
docker compose version
docker compose ps
```

### 6. ตั้ง Branch Protection

ดูรายละเอียดเต็มได้ที่ [BRANCH_PROTECTION_CHECKLIST.md](/d:/Projects/Bitkub_Bot/deploy/BRANCH_PROTECTION_CHECKLIST.md)

ค่าที่แนะนำ:

- require pull request before merging
- require approvals อย่างน้อย 1
- require status checks to pass before merging
- require branches to be up to date before merging
- required check: `CI / Validate`
- block force pushes
- block branch deletion
- optional: require linear history

## วิธีทำงานประจำวัน

### 1. แตก branch จาก `main`

ตัวอย่าง:

```bash
git switch main
git pull --ff-only origin main
git switch -c fixbug/some-fix
```

### 2. ทำงานและ commit บน branch นั้น

ตัวอย่าง:

```bash
git add .
git commit -m "Fix some issue"
git push -u origin fixbug/some-fix
```

### 3. เปิด PR เข้า `main`

เมื่อเปิด PR:

- `CI` จะรันอัตโนมัติ
- ถ้า `CI` ไม่ผ่าน ห้าม merge
- ถ้า `CI` ผ่าน ค่อย review แล้ว merge

### 4. Merge เข้า `main`

เมื่อ merge สำเร็จ:

- GitHub จะรัน `CI` บน `main` อีกครั้ง
- ถ้า `CI` บน `main` ผ่าน `deploy.yml` จะเริ่ม deploy อัตโนมัติ

### 5. ตรวจหลัง deploy

บน GitHub:

- ดูว่า `CI` ผ่าน
- ดูว่า `Deploy Production` ผ่าน

บน server:

```bash
cd /opt/bitkub/Bitkub_Bot
git rev-parse --short=12 HEAD
docker compose ps
docker compose logs -f engine
docker compose logs -f streamlit
```

บน UI:

- ดูหน้า Deployment
- version ควรขึ้นประมาณ `main@<commit>`
- ไม่ควรขึ้น `Version unknown`

## Manual Deploy

ถ้าต้องการ deploy เองจาก GitHub:

- ไปที่ `Actions`
- เลือก workflow `Deploy Production`
- กด `Run workflow`
- ระบุ `deploy_branch` ถ้าต้องการ

ถ้าต้องการทดสอบตรงบน server:

```bash
cd /opt/bitkub/Bitkub_Bot
bash deploy/deploy_prod.sh
```

## Troubleshooting

### 1. `Version unknown`

สาเหตุที่เคยเจอ:

- service อ่าน `git` metadata ไม่ได้
- deploy ยังไม่เขียน version metadata

สิ่งที่ระบบทำไว้แล้ว:

- `deploy_prod.sh` จะเขียน `.bitkub-app-version.json`
- `version_service.py` จะ fallback ไปอ่านไฟล์นี้

ถ้ายังไม่ขึ้น:

- เช็กว่า deploy รอบล่าสุดผ่านไหม
- เช็กว่าไฟล์ `.bitkub-app-version.json` อยู่ใน app root
- restart service แล้วลองใหม่

### 2. `importlib ... get_data` ตอน import โมดูล

เคสนี้มักเกิดจาก permission ของไฟล์ใน repo บน server

คำสั่งแก้ฉุกเฉิน:

```bash
sudo chown -R bitkub:bitkub /opt/bitkub/Bitkub_Bot
docker compose up -d --remove-orphans
```

ตอนนี้ `deploy_prod.sh` ใช้ Docker UID/GID จากผู้ใช้ที่ SSH เข้าไป และไม่พึ่ง systemd restart แล้ว

### 3. `Invalid workflow file`

ให้ตรวจ:

- YAML indentation
- heredoc ใต้ `run: |`
- expression ใน `${{ ... }}`

จุดพังที่เจอบ่อยสุดคือโค้ด Python ใต้ `python - <<'PY'` หลุด indentation ออกจาก block

### 4. deploy ผ่าน แต่ app ไม่อัปเดต

เช็กตามนี้:

```bash
cd /opt/bitkub/Bitkub_Bot
git rev-parse HEAD
journalctl -u bitkub-engine -n 100 --no-pager
journalctl -u bitkub-streamlit -n 100 --no-pager
```

ดูว่า commit ที่ server ตรงกับ commit ที่ GitHub เพิ่ง deploy หรือไม่

## ลำดับแนะนำสำหรับการใช้งานจริง

1. ตั้ง SSH key
2. ตั้ง GitHub Secrets
3. ให้ server pull repo ได้
4. ตั้ง sudoers
5. ทดสอบ `bash deploy/deploy_prod.sh` บน droplet ด้วยมือ 1 รอบ
6. push branch แล้วเปิด PR
7. ให้ `CI` ผ่าน
8. merge เข้า `main`
9. ดู `CI` บน `main`
10. ดู `Deploy Production`
11. ตรวจ version บน UI และ service logs

## เอกสารอ้างอิงใน repo

- [generate_new_ssh_key_github_actions.md](/d:/Projects/Bitkub_Bot/docs/generate_new_ssh_key_github_actions.md)
- [DEPLOY_SECRETS_CHECKLIST.md](/d:/Projects/Bitkub_Bot/deploy/DEPLOY_SECRETS_CHECKLIST.md)
- [BRANCH_PROTECTION_CHECKLIST.md](/d:/Projects/Bitkub_Bot/deploy/BRANCH_PROTECTION_CHECKLIST.md)
- [VPS_DEPLOY.md](/d:/Projects/Bitkub_Bot/deploy/VPS_DEPLOY.md)

