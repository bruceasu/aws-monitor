# 安装依赖
DB lib
> pip install psycopg2-binary openai
如果后面要把 AWS/RDS 采集也改成 Python，再加：
> pip install boto3

# 用环境变量传数据库连接信息：
```bash
export PGHOST="your-rds-endpoint.ap-northeast-1.rds.amazonaws.com"
export PGPORT="5432"
export PGDATABASE="your_db"
export PGUSER="readonly_user"
export PGPASSWORD="your_password"
```

# 建议给只读用户加：
`GRANT pg_monitor TO readonly_user;`


# 运行：
```bash
python3 collect_pg_diag.py \
  --out pg_diag_20260425_1100 \
  --long-query-seconds 30 \
  --long-xact-seconds 120
```

# 运行：
```bash
export OPENAI_API_KEY="你的 key"
python3 analyze_pg_diag_ai.py \
  --input pg_diag_20260425_1100 \
  --output pg_diag_20260425_1100/ai_pg_rca_report.md \
  --model gpt-4.1 \
  --dump-evidence
```

#  建议最终改成这个结构
```text
incident_20260425_1100/
  aws_rds/
    RDS / CloudWatch / Performance Insights 采集结果
  postgresql/
    PostgreSQL 内部采集结果
  final_rca_report.md
```


这样最后可以变成一个统一命令：
```bash
python3 collect_pg_diag.py \
  --out pg_diag_20260425_1100 \
  --long-query-seconds 30 \
  --long-xact-seconds 120
  
python3 collect_aws_rds_diag.py \
  --db-instance prod-rds-01 \
  --region ap-northeast-1 \
  --start "2026-04-25T10:00:00+09:00" \
  --end "2026-04-25T12:30:00+09:00" \
  --out incident_20260425_1100/aws_rds

```
然后：
```bash
python3 analyze_incident_ai.py \
  --input incident_20260425_1100 \
  --output incident_20260425_1100/final_rca_report.md
```

