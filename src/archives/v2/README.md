# 1. 精确时间范围，推荐
```bash
./01_rds_collect.sh \
 --db prod-rds-01 \
 --region ap-northeast-1 \
 --start "2026-04-25T10:30:00+09:00" \
 --end "2026-04-25T12:00:00+09:00"
```

# 2. 或者仍然使用最近 N 小时
```bash
./01_rds_collect.sh \
 --db prod-rds-01 \
 --region ap-northeast-1 \
 --hours 24
```

# 3. 建议你的实际运行方式
```bash
# 假设故障发生在日本时间 `2026-04-25 11:00` 左右，建议采集故障前后各 60–90 分钟：
./rds_collect.sh \
 --db prod-rds-01 \
 --region ap-northeast-1 \
 --start "2026-04-25T09:30:00+09:00" \
 --end "2026-04-25T12:30:00+09:00" \
 --period 60
# 然后调用 AI 分析：
export OPENAI_API_KEY="你的 key"
python3 ai_analyze_rds.py \
 --input rds_diag_prod-rds-01_20260425_030000 \
 --output rds_diag_prod-rds-01_20260425_030000/ai_rca_report.md \
 --model gpt-4.1 \
 --dump-evidence
# 如果时间窗口很长，比如 24 小时，建议把 --period 改成 300：
./rds_collect.sh \
 --db prod-rds-01 \
 --region ap-northeast-1 \
 --start "2026-04-24T00:00:00+09:00" \
 --end "2026-04-25T00:00:00+09:00" \
 --period 300
```

# 4. 建议补充：故障点前后窗口
```bash
# 可以跑三次，分别比较：
# 故障前基线
./rds_collect.sh --db prod-rds-01 --region ap-northeast-1 \
 --start "2026-04-25T08:00:00+09:00" \
 --end "2026-04-25T10:00:00+09:00"
# 故障窗口
#./rds_collect.sh --db prod-rds-01 --region ap-northeast-1 \
 --start "2026-04-25T10:00:00+09:00" \
 --end "2026-04-25T12:00:00+09:00"
# 重启后恢复窗口
#./rds_collect.sh --db prod-rds-01 --region ap-northeast-1 \
 --start "2026-04-25T12:00:00+09:00" \
 --end "2026-04-25T14:00:00+09:00"
```
这样 AI 分析时可以看出是：
正常基线 → 异常恶化 → 重启后恢复
这比只看故障窗口更容易判断根因。

# 5. 你后续最应该看的输出
优先看：
- ai_rca_report.md
- events.json
- perf_insights_top_waits.json
- perf_insights_top_sql.json
- logs/*.txt
- metrics/CPUUtilization.json
- metrics/DatabaseConnections.json
- metrics/FreeableMemory.json
- metrics/SwapUsage.json
- metrics/FreeStorageSpace.json
- metrics/WriteLatency.json
- metrics/DiskQueueDepth.json
如果 AI 报告里出现下面这些结论，基本可以直接进入对应处理：
|AI 结论|优先动作|
|------|--------|
|Too many connections|	调小连接池、查连接泄漏、加 RDS Proxy|
|FreeableMemory low + Swap| high	升配、降连接数、查大查询|
|WriteLatency / DiskQueueDepth high|	查大写入、慢 SQL、提高 gp3 IOPS/吞吐|
|FreeStorageSpace low|	立即扩容、清理 binlog/WAL/日志|
|failover/reboot event|	查触发前指标，确认维护/参数变更/实例压力|
|Top Waits = lock|	查长事务、死锁、DDL、批量更新|
|Top Waits = IO|	查全表扫描、索引、存储性能|
|Top SQL 单条负载极高|	先优化该 SQL，不要盲目扩容|


# postgresql 内部信息支持

## 1. PostgreSQL 内部采集脚本：pg_collect.sh

## 2. AI 分析脚本：ai_analyze_pg.py

## 3. 推荐实际执行流程

### 第一步：事故发生后立即采集 PostgreSQL 内部状态
这个脚本采的是**当前数据库内部状态**，所以越接近故障现场越有价值。重启之后再采，很多锁、长事务、连接状态已经消失。
```bash
export PGHOST="your-rds-endpoint.xxxxxx.ap-northeast-1.rds.amazonaws.com"
export PGPORT="5432"
export PGDATABASE="your_db"
export PGUSER="readonly_user"
export PGPASSWORD="your_password"
./pg_collect.sh \
  --out pg_diag_incident_20260425_1100 \
  --long-query-seconds 30 \
  --long-xact-seconds 120 \
  --lock-wait-seconds 5
```
### 第二步：合并 AWS/RDS 采集结果做 AI 分析
```bash
export OPENAI_API_KEY="你的 key"
python3 ai_analyze_pg.py \
  --pg-input pg_diag_incident_20260425_1100 \
  --aws-input rds_diag_prod-rds-01_20260425_110000 \
  --output final_rds_postgres_rca.md \
  --model gpt-5.4 \
  --dump-evidence
```

如果你暂时没有 AWS/RDS 采集目录，也可以只分析 PostgreSQL：
```bash
python3 ai_analyze_pg.py \
  --pg-input pg_diag_incident_20260425_1100 \
  --output pg_only_rca.md \
  --model gpt-5.4
```

## 4. 这个脚本重点能抓出的根因
|采集文件|用途|
|-------|---|
|04_long_running_queries.json|	当前长 SQL|
|05_long_transactions.json|	长事务，可能阻塞 vacuum 或持锁|
|06_idle_in_transaction.json|	典型事故源：应用开事务后不提交|
|07_blocking_locks.json|	谁阻塞谁，最关键|
|09_waiting_sessions.json|	等待中的会话，判断 IO/Lock/Client/Extension|
|19_top_statements.json|	总耗时最高 SQL，需要 pg_stat_statements|
|20_top_statements_io.json|	IO 消耗最高 SQL|
|10_db_stats.json|	deadlock、temp files、cache hit、rollback|
|11_top_tables_activity.json|	死元组、autovacuum 线索|
|12_missing_index_candidates.json|	大量顺序扫描候选表|
|17_bgwriter_checkpoint.json|	checkpoint 压力|
|15_replication_slots.json|	replication slot 导致 WAL 堆积|
|25_active_ddl.json|	DDL / vacuum / index 操作导致锁或 IO|

5. 如果你还没有启用 pg_stat_statements
建议启用。否则只能看到当前正在跑的 SQL，看不到过去累计的 Top SQL。
RDS PostgreSQL 通常需要在参数组中设置：
> shared_preload_libraries = 'pg_stat_statements'
然后重启实例，再在数据库中执行：
> CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
常用参数：
> pg_stat_statements.track = all
> pg_stat_statements.max = 10000
注意：修改 shared_preload_libraries 通常需要重启。生产环境要走维护窗口。

## 6. 事故现场可选的“止血 SQL”
这些不是脚本默认执行的，因为有风险，但 RCA 报告可能会建议你人工执行。
查看阻塞链
```sql
SELECT
  blocked.pid AS blocked_pid,
  blocked.usename AS blocked_user,
  blocked.application_name AS blocked_app,
  now() - blocked.query_start AS blocked_age,
  left(blocked.query, 1000) AS blocked_query,
  blocker.pid AS blocker_pid,
  blocker.usename AS blocker_user,
  blocker.application_name AS blocker_app,
  now() - blocker.query_start AS blocker_age,
  left(blocker.query, 1000) AS blocker_query
FROM pg_stat_activity blocked
JOIN LATERAL unnest(pg_blocking_pids(blocked.pid)) AS bpid(pid) ON true
JOIN pg_stat_activity blocker ON blocker.pid = bpid.pid
ORDER BY blocked_age DESC;
```
取消某个查询，风险较低
`SELECT pg_cancel_backend(<pid>);`
强制断开某个连接，风险较高
`SELECT pg_terminate_backend(<pid>);`
pg_terminate_backend 会中断事务，可能导致应用报错、事务回滚、连接池重连风暴。不要批量盲杀。

## 7. 最终建议的目录结构
建议你的 RCA 流程统一成这样：
```text
incident_20260425_1100/
  aws_rds/
    rds_collect.sh 输出
  postgresql/
    pg_collect.sh 输出
  final_rds_postgres_rca.md
```

运行示例：
```bash
mkdir -p incident_20260425_1100
./rds_collect.sh \
  --db prod-rds-01 \
  --region ap-northeast-1 \
  --start "2026-04-25T10:00:00+09:00" \
  --end "2026-04-25T12:30:00+09:00" \
  --period 60
./pg_collect.sh \
  --out incident_20260425_1100/postgresql \
  --long-query-seconds 30 \
  --long-xact-seconds 120 \
  --lock-wait-seconds 5
python3 ai_analyze_pg.py \
  --pg-input incident_20260425_1100/postgresql \
  --aws-input rds_diag_prod-rds-01_20260425_110000 \
  --output incident_20260425_1100/final_rds_postgres_rca.md \
  --model gpt-5.4 \
  --dump-evidence
```