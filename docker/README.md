# 创建容器
## 构建镜像
docker build -t rds-incident-rca:latest .
## 验证镜像：
docker run --rm rds-incident-rca:latest --version


## 建议 PostgreSQL 诊断用户使用：
```sql
CREATE USER rds_diag_user WITH PASSWORD 'change_me';
GRANT CONNECT ON DATABASE your_database TO rds_diag_user;
GRANT pg_monitor TO rds_diag_user;
```
如果要读取 pg_stat_statements：
`GRANT SELECT ON pg_stat_statements TO rds_diag_user;`

注意：env\pg.env 里有密码，不要提交到 Git。

在 env\openai.env 写入：
> OPENAI_API_KEY=你的_OpenAI_API_Key
如果你不想落地保存 API key，也可以运行时传：
> -e OPENAI_API_KEY="$env:OPENAI_API_KEY"

## 运行 AWS/RDS 采集
假设事故发生在日本时间 2026-04-25 10:00 - 12:30。
PowerShell 命令：
```powershell
docker run --rm `
  -v "${PWD}\output:/app/output" `
  -v "$env:USERPROFILE\.aws:/root/.aws:ro" `
  -e AWS_PROFILE=your-profile `
  rds-incident-rca:latest `
  /app/scripts/collect_aws_rds_diag.py `
    --db-instance prod-rds-01 `
    --region ap-northeast-1 `
    --start "2026-04-25T10:00:00+09:00" `
    --end "2026-04-25T12:30:00+09:00" `
    --period 60 `
    --out /app/output/aws_rds
```    
输出：
```text
output\aws_rds
  ├── metadata.json
  ├── db_instance.json
  ├── events.json
  ├── pending_maintenance.json
  ├── cloudwatch_alarms.json
  ├── metric_summaries.json
  ├── performance_insights.json
  ├── logs\
  └── aws_rds_summary.md
```  
如果你不用 profile，而是直接使用默认凭证，可以去掉：
> -e AWS_PROFILE=your-profile

## 运行 PostgreSQL 内部采集
先确认本地到 RDS 端口可通：
> Test-NetConnection your-rds-endpoint.ap-northeast-1.rds.amazonaws.com -Port 5432
然后运行：
```pwsh
docker run --rm `
  -v "${PWD}\output:/app/output" `
  --env-file .\env\pg.env `
  rds-incident-rca:latest `
  /app/scripts/collect_pg_diag.py `
    --out /app/output/postgresql `
    --long-query-seconds 30 `
    --long-xact-seconds 120
```    
输出：
```text
output\postgresql
  ├── metadata.json
  ├── version.json
  ├── settings_core.json
  ├── connection_summary.json
  ├── long_running_queries.json
  ├── long_transactions.json
  ├── idle_in_transaction.json
  ├── blocking_locks.json
  ├── pg_stat_statements_top_total_time.json
  └── ...
```  
注意：PostgreSQL 内部状态是“当前快照”。如果数据库已经重启，很多锁、长事务、活动 SQL 已经消失；这种情况下主要依赖 AWS/RDS Events、CloudWatch、RDS logs、Performance Insights 和 pg_stat_statements 的累计信息。

## 运行 AI RCA 分析
```pwsh
docker run --rm `
  -v "${PWD}\output:/app/output" `
  --env-file .\env\openai.env `
  rds-incident-rca:latest `
  /app/scripts/analyze_incident_ai.py `
    --aws-input /app/output/aws_rds `
    --pg-input /app/output/postgresql `
    --output /app/output/final_rca_report.md `
    --model gpt-4.1 `
    --dump-evidence
```
输出：
```text
output\final_rca_report.md
output\ai_evidence_pack.json
```

## 一键运行 Powershell 脚本
运行：
```pwsh
.\run_incident.ps1 `
  -DbInstance "prod-rds-01" `
  -Region "ap-northeast-1" `
  -Start "2026-04-25T10:00:00+09:00" `
  -End "2026-04-25T12:30:00+09:00" `
  -AwsProfile "your-profile" `
  -Model "gpt-4.1"
```

# 不创建容器
你可以不创建 Docker 镜像，直接用官方 python:3.12-slim 镜像临时启动容器，然后挂载 Windows 文件夹，在容器内安装依赖并执行脚本。
适合你当前验证阶段。缺点是每次都会重新安装依赖，速度慢一些；优点是不需要维护 Dockerfile。

## 方案 A：一次性执行，不进入容器
假设你的目录是：
```text
C:\rds-incident-rca
  ├── scripts\
  │   ├── collect_aws_rds_diag.py
  │   ├── collect_pg_diag.py
  │   └── analyze_incident_ai.py
  ├── output\
  └── env\
      ├── pg.env
      └── openai.env
```
在 PowerShell 进入目录：
> cd C:\rds-incident-rca
1. 临时运行 AWS/RDS 采集
```pwsh
docker run --rm `
  -v "${PWD}:/work" `
  -v "$env:USERPROFILE\.aws:/root/.aws:ro" `
  -e AWS_PROFILE=your-profile `
  -w /work `
  python:3.12-slim `
  bash -lc "pip install --no-cache-dir boto3 botocore && python scripts/collect_aws_rds_diag.py --db-instance prod-rds-01 --region ap-northeast-1 --start '2026-04-25T10:00:00+09:00' --end '2026-04-25T12:30:00+09:00' --period 60 --out output/aws_rds"
```  
2. 临时运行 PostgreSQL 内部采集
python:3.12-slim 里没有编译依赖，psycopg2-binary 通常可以直接装；为了稳妥，不需要装 postgresql-client，因为脚本用的是 Python driver。
```pwsh
docker run --rm `
  -v "${PWD}:/work" `
  --env-file .\env\pg.env `
  -w /work `
  python:3.12-slim `
  bash -lc "pip install --no-cache-dir psycopg2-binary && python scripts/collect_pg_diag.py --out output/postgresql --long-query-seconds 30 --long-xact-seconds 120"
```  
3. 临时运行 AI 分析
```pwsh
docker run --rm `
  -v "${PWD}:/work" `
  --env-file .\env\openai.env `
  -w /work `
  python:3.12-slim `
  bash -lc "pip install --no-cache-dir openai && python scripts/analyze_incident_ai.py --aws-input output/aws_rds --pg-input output/postgresql --output output/final_rca_report.md --model gpt-4.1 --dump-evidence"
```  
生成结果：
> C:\rds-incident-rca\output\final_rca_report.md

## 方案 B：启动一个临时交互容器，手动执行多条命令
这个方式更适合调试，因为依赖只在这个容器生命周期内安装一次。
```pwsh
docker run --rm -it `
  -v "${PWD}:/work" `
  -v "$env:USERPROFILE\.aws:/root/.aws:ro" `
  --env-file .\env\pg.env `
  --env-file .\env\openai.env `
  -e AWS_PROFILE=your-profile `
  -w /work `
  python:3.12-slim `
  bash
```  
进入容器后执行：
> pip install --no-cache-dir boto3 botocore psycopg2-binary openai
然后依次运行：
```bash
python scripts/collect_aws_rds_diag.py \
  --db-instance prod-rds-01 \
  --region ap-northeast-1 \
  --start "2026-04-25T10:00:00+09:00" \
  --end "2026-04-25T12:30:00+09:00" \
  --period 60 \
  --out output/aws_rds
python scripts/collect_pg_diag.py \
  --out output/postgresql \
  --long-query-seconds 30 \
  --long-xact-seconds 120
python scripts/analyze_incident_ai.py \
  --aws-input output/aws_rds \
  --pg-input output/postgresql \
  --output output/final_rca_report.md \
  --model gpt-4.1 \
  --dump-evidence
```  
退出容器：
> exit
容器删除后，pip install 的依赖会消失，但 output 目录里的结果会保留在 Windows 上。

## 方案 C：不做镜像，但挂载 pip 缓存，避免每次下载依赖
如果你不想 build image，又想减少重复下载，可以建一个本地 pip cache：
> mkdir C:\rds-incident-rca\.pip-cache
运行时挂载：
```pwsh
docker run --rm `
  -v "${PWD}:/work" `
  -v "${PWD}\.pip-cache:/root/.cache/pip" `
  -v "$env:USERPROFILE\.aws:/root/.aws:ro" `
  -e AWS_PROFILE=your-profile `
  -w /work `
  python:3.12-slim `
  bash -lc "pip install boto3 botocore psycopg2-binary openai && python scripts/collect_aws_rds_diag.py --db-instance prod-rds-01 --region ap-northeast-1 --start '2026-04-25T10:00:00+09:00' --end '2026-04-25T12:30:00+09:00' --period 60 --out output/aws_rds"
```  
这样每次依赖还会安装，但下载会快很多。

## 方案 D：用命名 volume 保存 Python 依赖，不创建镜像
你也可以用 Docker volume 保存一个虚拟环境。这样不 build image，但依赖可复用。
第一次创建依赖：
> docker volume create rds_rca_venv
初始化虚拟环境：
```pwsh
docker run --rm `
  -v rds_rca_venv:/venv `
  python:3.12-slim `
  bash -lc "python -m venv /venv && /venv/bin/pip install --upgrade pip && /venv/bin/pip install boto3 botocore psycopg2-binary openai"
```  
之后运行 AWS 采集：
```pwsh
docker run --rm `
  -v rds_rca_venv:/venv `
  -v "${PWD}:/work" `
  -v "$env:USERPROFILE\.aws:/root/.aws:ro" `
  -e AWS_PROFILE=your-profile `
  -w /work `
  python:3.12-slim `
  /venv/bin/python scripts/collect_aws_rds_diag.py `
    --db-instance prod-rds-01 `
    --region ap-northeast-1 `
    --start "2026-04-25T10:00:00+09:00" `
    --end "2026-04-25T12:30:00+09:00" `
    --period 60 `
    --out output/aws_rds
```    
运行 PostgreSQL 采集：
```pwsh
docker run --rm `
  -v rds_rca_venv:/venv `
  -v "${PWD}:/work" `
  --env-file .\env\pg.env `
  -w /work `
  python:3.12-slim `
  /venv/bin/python scripts/collect_pg_diag.py `
    --out output/postgresql `
    --long-query-seconds 30 `
    --long-xact-seconds 120
```    
运行 AI 分析：
```pwsh
docker run --rm `
  -v rds_rca_venv:/venv `
  -v "${PWD}:/work" `
  --env-file .\env\openai.env `
  -w /work `
  python:3.12-slim `
  /venv/bin/python scripts/analyze_incident_ai.py `
    --aws-input output/aws_rds `
    --pg-input output/postgresql `
    --output output/final_rca_report.md `
    --model gpt-4.1 `
    --dump-evidence
```    
这个方案我比较推荐：不需要创建镜像，但依赖能复用。


# Docker compose
## step by step
```bash
docker compose run --rm aws_collect
docker compose run --rm pg_collect
docker compose run --rm analyze
```

## 一键串行
```pwsh
docker compose run --rm aws_collect; `
docker compose run --rm pg_collect; `
docker compose run --rm analyze
```

## 创建新脚本
scripts/run_incident.py

```python
import os
import subprocess
import json
from datetime import datetime
from pathlib import Path
import zipfile

BASE_DIR = Path("/work")
OUTPUT_DIR = BASE_DIR / "output"

def run(cmd: list[str]):
    print(f"[RUN] {' '.join(cmd)}")
    result = subprocess.run(cmd)
    if result.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(cmd)}")

def create_incident_dir():
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    incident_dir = OUTPUT_DIR / f"incident_{ts}"
    incident_dir.mkdir(parents=True, exist_ok=True)
    return incident_dir

def write_meta(incident_dir):
    meta = {
        "timestamp": datetime.now().isoformat(),
        "db_instance": os.getenv("DB_INSTANCE"),
        "region": os.getenv("AWS_REGION"),
        "start_time": os.getenv("START_TIME"),
        "end_time": os.getenv("END_TIME"),
        "model": os.getenv("MODEL"),
    }
    (incident_dir / "incident_meta.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False)
    )

def zip_incident(incident_dir):
    zip_path = incident_dir / "incident_package.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for path in incident_dir.rglob("*"):
            if path.is_file():
                z.write(path, path.relative_to(incident_dir))
    print(f"[DONE] zipped: {zip_path}")

def main():
    incident_dir = create_incident_dir()

    aws_dir = incident_dir / "aws_rds"
    pg_dir = incident_dir / "postgresql"

    write_meta(incident_dir)

    # 1. AWS 采集
    run([
        "python", "scripts/collect_aws_rds_diag.py",
        "--db-instance", os.environ["DB_INSTANCE"],
        "--region", os.environ["AWS_REGION"],
        "--start", os.environ["START_TIME"],
        "--end", os.environ["END_TIME"],
        "--period", "60",
        "--out", str(aws_dir)
    ])

    # 2. PostgreSQL 采集
    run([
        "python", "scripts/collect_pg_diag.py",
        "--out", str(pg_dir),
        "--long-query-seconds", "30",
        "--long-xact-seconds", "120"
    ])

    # 3. AI 分析
    run([
        "python", "scripts/analyze_incident_ai.py",
        "--aws-input", str(aws_dir),
        "--pg-input", str(pg_dir),
        "--output", str(incident_dir / "final_rca_report.md"),
        "--model", os.environ["MODEL"],
        "--dump-evidence"
    ])

    # 4. 打包
    zip_incident(incident_dir)

    print(f"\n[DONE] Incident folder: {incident_dir}")

if __name__ == "__main__":
    main()
```

```bash
docker compose run --rm incident
```


Windows Task Scheduler 执行命令

Program:

powershell.exe

Arguments:

-NoProfile -ExecutionPolicy Bypass -Command "cd C:\rds-incident-rca; docker compose run --rm trigger"

频率：

每 5 分钟

SSM Parameter Store 配置
1️⃣ 存密码
aws ssm put-parameter \
  --name "/rca/db/password" \
  --value "your_password" \
  --type "SecureString"

docker compose run --rm incident python scripts/execute_runbook.py `
  --findings output/incident_xxx/findings.json `
  --out output/incident_xxx/runbook_execution.json  

docker compose run --rm incident python scripts/execute_runbook.py `
  --findings output/incident_xxx/findings.json `
  --out output/incident_xxx/runbook_execution.json `
  --execute  