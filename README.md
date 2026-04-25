# 1. 先确认：到底是 RDS 宕机，还是应用连接不上
先看 AWS 控制台：
> `RDS → Databases → 目标实例 → Connectivity & security / Monitoring / Logs & events`

重点确认：
|现象|可能含义|
|---|--------|
|DB instance status 变成 rebooting、modifying、backing-up、storage-optimization|可能是维护、参数变更、存储调整或自动重启
|Multi-AZ 发生 failover|主库故障、AZ 问题、底层维护、实例压力过高|
|RDS 状态正常，但应用报连接失败|多半是连接数、网络、安全组、DNS、连接池或数据库内部锁/慢 SQL|
|只有高峰期不可用|常见是 CPU、内存、IOPS、连接数或慢 SQL 问题|
|突然大量 Too many connections|连接池配置、连接泄漏、短连接暴增|

AWS 官方建议通过 CloudWatch、Enhanced Monitoring、Performance Insights、RDS logs/events 组合排查，不能只看一个指标。RDS 会把实例指标发布到 CloudWatch；Enhanced Monitoring 可以看到更细的 OS 指标；Performance Insights 用于分析数据库负载和 SQL。(AWS Documentation)

# 2. 第一优先级：查 RDS Events
先查最近一次故障时间点前后 30 分钟的事件。

路径：
> `RDS → Databases → 目标实例 → Logs & events → Recent events`
重点找这些关键词：
- failover
- reboot
- restarted
- crash
- storage
- maintenance
- backup
- patching
- low storage
- out of memory
- DB instance shutdown
- DB instance restarted
- DB instance failover completed

如果是 Multi-AZ，RDS 故障转移通常会有 60–120 秒左右中断，但大事务或恢复过程较长时会更久。(AWS Documentation)

如果你看到：
- Multi-AZ failover completed

说明这不是简单的应用连接问题，而是主库发生了切换。要继续查 failover 之前的 CPU、内存、IO、连接数、存储、错误日志。

# 3. 查 CloudWatch 关键指标
进入：
> `RDS → Monitoring → CloudWatch metrics`
优先看故障时间点前后这些指标。

## A. CPU
看：
- CPUUtilization

判断：
|指标表现|结论|
|-------|----|
|长时间 > 80%|实例规格不足、SQL 消耗高、缺索引、并发过高|
|突然打满到 100%|突发 SQL、批处理、报表、死循环请求、连接风暴|
|CPU 不高但应用超时|可能是锁、IO、连接数、网络或数据库等待|

处理方向：
1. 打开 Performance Insights
2. 找 Top SQL
3. 查慢查询日志
4. 检查是否缺索引
5. 必要时临时升配

## B. 内存
看：
- FreeableMemory
- SwapUsage

判断：
|指标表现|结论|
|-------|---|
|FreeableMemory 持续下降|内存压力、连接过多、缓存配置过大|
|SwapUsage 增长|实例内存不足，性能会明显恶化|
|故障前内存接近耗尽|可能触发数据库进程异常或 RDS 自动恢复|

处理方向：
1. 减少最大连接数
2. 调整连接池
3. 优化大查询/排序/临时表
4. 增大实例规格
5. 检查参数组里的内存相关参数

## C. 连接数
看：
- DatabaseConnections

同时在数据库里查最大连接数。
- MySQL / MariaDB：
    - SHOW VARIABLES LIKE 'max_connections';
    - SHOW STATUS LIKE 'Threads_connected';
    - SHOW PROCESSLIST;
- PostgreSQL：
    - SHOW max_connections;
    - SELECT count(*) FROM pg_stat_activity;
    - SELECT state, count(*) FROM pg_stat_activity GROUP BY state;

判断：
|现象|可能原因|
|---|--------|
|DatabaseConnections 突然飙升|应用扩容、连接池过大、连接泄漏、重试风暴|
|接近 max_connections|新连接失败，应用以为 RDS 宕机|
|大量 idle 连接|连接池回收配置不合理|
|大量 active 连接|SQL 慢、锁等待、并发过高|

一个非常常见的问题是：ECS / EC2 / Lambda 扩容后，每个实例都有连接池，最终把 RDS 连接数打满。
例如：
> 20 个 ECS task × 每个 HikariCP maximumPoolSize=30`
> = 600 个潜在连接`
如果 RDS max_connections 只有 300，很容易雪崩。

## D. 存储空间
看：
- FreeStorageSpace

判断：
|指标表现|结论|
|------|----|
|FreeStorageSpace 接近 0|数据库可能不可写，甚至异常|
|下降很快|日志、binlog、临时表、批量写入、未清理历史数据|
|开了自动扩容但仍异常|可能增长过快，或达到上限|

处理方向：
1. 立即确认 FreeStorageSpace
2. 清理无用数据/日志
3. 增加 allocated storage
4. 开启 storage autoscaling
5. 检查 binlog / WAL / long transaction

RDS 的 CloudWatch 指标包括 CPU、连接、存储、IO 等，用于排查性能和可用性问题。(AWS Documentation)

# E. 磁盘 IO / 延迟
看：
- ReadIOPS
- WriteIOPS
- ReadLatency
- WriteLatency
- DiskQueueDepth
- EBSByteBalance%
- EBSIOBalance%

判断：
|现象|可能原因|
|---|--------|
|WriteLatency 飙升|写入压力、事务太大、索引过多、磁盘性能不足|
|DiskQueueDepth 很高|IO 排队，数据库会变慢|
|IOPS 到顶|gp2/gp3/io1/io2 配置不足|
|CPU 不高但 SQL 很慢|很可能是 IO 瓶颈或锁等待|

处理方向：
1. 检查存储类型：gp2 / gp3 / io1 / io2
2. gp3 可单独提高 IOPS 和吞吐
3. 查 Top SQL 是否大量扫表
4. 检查是否有大批量写入、报表、备份、ETL

# 4. 打开 Performance Insights 查 Top SQL
如果 RDS 不是状态级故障，而是“卡死/连不上/超时”，Performance Insights 很关键。
路径：
> RDS → Performance Insights
重点看：
- DB Load
- Top waits
- Top SQL
- Top users
- Top hosts

判断逻辑：
|Wait 类型|常见原因|
|--------|--------|
|CPU|SQL 消耗 CPU、缺索引、函数计算多|
|IO全表扫描、大查询、磁盘性能不足|
|Lock|行锁/表锁/长事务|
|Connection|连接数过高|
|Buffer / memory|缓存不足、内存压力|

AWS 官方说明 Performance Insights 是用于分析影响数据库性能问题的功能。(AWS Documentation)

如果 Top SQL 里看到某条 SQL 占了大部分 DB Load，优先优化它，通常比盲目升配更有效。

# 5. 查数据库错误日志和慢查询日志
## MySQL / MariaDB
在 RDS 控制台看：
- error/mysql-error-running.log
- slowquery/mysql-slowquery.log
- general/mysql-general.log

建议参数：
- slow_query_log = 1
- long_query_time = 1 或 2
- log_queries_not_using_indexes = 1   # 谨慎开启，可能日志很多

常见错误：
- Too many connections
- Out of memory
- Deadlock found
- Lock wait timeout exceeded
- Table is full
- No space left on device
- Aborted connection

## PostgreSQL

查看：
- postgresql.log

建议参数：
- log_min_duration_statement = 1000
- log_lock_waits = on
- deadlock_timeout = 1s

常见错误：
- remaining connection slots are reserved
- out of memory
- could not write to file
- deadlock detected
- terminating connection due to administrator command


RDS 官方也强调完整排查需要结合 metrics、events、logs 和 activity streams。(AWS Documentation)

# 6. 查是否被自动维护、备份、参数变更影响
确认这些配置：
- Maintenance window
- Backup window
- Pending modifications
- Parameter group changes
- Option group changes
- Minor version auto upgrade

重点问题：
|配置|风险|
|---|---|
|维护窗口在业务高峰|自动维护可能造成短暂不可用|
|参数修改需要 reboot|下次维护或手动操作时会重启|
|自动小版本升级|可能触发维护|
|备份窗口与高峰重叠|IO 压力可能增加|

建议：
1. 维护窗口放到业务低峰
2. 备份窗口避开批处理/结算/报表时间
3. 所有 pending modifications 都要记录变更人和时间
4. 参数组变更走变更流程

# 7. 检查应用连接池，尤其是 Spring Boot / HikariCP
如果你是 Spring Boot，重点看 HikariCP 配置。
常见危险配置：
```yaml
spring:
  datasource:
    hikari:
      maximum-pool-size: 50
```

如果 ECS 有很多 task，这个值很容易把 RDS 打爆。
建议先保守配置：
```yaml
spring:
  datasource:
    hikari:
      maximum-pool-size: 10
      minimum-idle: 2
      connection-timeout: 3000
      idle-timeout: 600000
      max-lifetime: 1800000
      validation-timeout: 3000
```      
还要确认：
1. 应用是否有连接泄漏
2. 是否每个请求都新建连接
3. Lambda 是否频繁创建新连接
4. 是否配置了失败重试导致连接风暴
5. 是否用了 RDS Proxy

如果有 Lambda、高并发短连接、频繁扩缩容，建议考虑 RDS Proxy，它可以缓解连接风暴和连接复用问题。

# 8. 检查是否是锁或长事务导致“假死”
## MySQL 查锁
```sql
SHOW FULL PROCESSLIST;
SELECT * 
FROM information_schema.innodb_trx;
SELECT * 
FROM information_schema.innodb_lock_waits;
```

## PostgreSQL 查锁
```sql
SELECT
  blocked_locks.pid AS blocked_pid,
  blocked_activity.query AS blocked_query,
  blocking_locks.pid AS blocking_pid,
  blocking_activity.query AS blocking_query
FROM pg_catalog.pg_locks blocked_locks
JOIN pg_catalog.pg_stat_activity blocked_activity 
  ON blocked_activity.pid = blocked_locks.pid
JOIN pg_catalog.pg_locks blocking_locks 
  ON blocking_locks.locktype = blocked_locks.locktype
JOIN pg_catalog.pg_stat_activity blocking_activity 
  ON blocking_activity.pid = blocking_locks.pid
WHERE NOT blocked_locks.granted;
```

如果数据库 CPU 不高、IO 不高，但大量请求超时，很可能是：
- 长事务
- DDL 锁表
- 批量更新
- 未提交事务
- 慢 SQL 占用连接

# 9. 检查网络、安全组、DNS
如果 RDS 状态正常，但应用连接失败，查：
- Security Group inbound/outbound
- NACL
- Route Table
- Subnet
- VPC peering / Transit Gateway
- DNS resolution
- RDS endpoint 是否写死 IP

重点：
不要把 RDS endpoint 解析后的 IP 写死。
RDS failover 后 endpoint 指向的后端会变化。如果应用或中间件缓存 DNS 太久，会导致 failover 后仍连旧地址。

Java 可以检查：
- networkaddress.cache.ttl
建议不要永久缓存 DNS。

# 10. 如果是 Multi-AZ，也要确认应用是否能正确处理 failover
Multi-AZ 不是零中断。failover 时应用连接会断，需要应用具备：
1. 连接失败后重试
2. 连接池能丢弃坏连接
3. DNS TTL 不要过长
4. 事务要能安全重试
5. 关键写操作要幂等

RDS Multi-AZ failover 通常会短暂中断，AWS 文档说明 failover 时间通常为 60–120 秒，但可能因事务恢复等原因变长。(AWS Documentation)

# 11. 建议立刻配置的告警
至少加这些 CloudWatch Alarm：

|指标|建议阈值|
|---|----------|
|CPUUtilization|> 80%，持续 5 分钟|
|FreeableMemory|低于实例内存 10–15%|
|SwapUsage|> 0 或持续增长|
|FreeStorageSpace|< 20% 或固定 GB 阈值|
|DatabaseConnections|> max_connections × 80%|
|ReadLatency / WriteLatency|明显高于平时基线|
|DiskQueueDepth|持续升高|
|ReplicaLag|读副本延迟过高|
|Deadlocks|> 0 持续出现|
|FailedSQLServerAgentJobs / engine-specific error|按引擎配置|

# 12. 推荐排查顺序

按这个顺序执行：
1. 确认故障时间点
2. 查 RDS Events：有没有 reboot / failover / maintenance
3. 查 CloudWatch：CPU、内存、连接数、存储、IO、延迟
4. 查 Performance Insights：Top SQL、Top waits
5. 查数据库 error log / slow query log
6. 查应用连接池和应用日志
7. 查是否有连接泄漏、重试风暴、ECS/Lambda 扩容
8. 查长事务、锁等待、死锁
9. 查安全组、DNS、VPC 网络
10. 根据根因决定：优化 SQL、调连接池、扩容、换存储、加 RDS Proxy、改架构

# 13. 常见根因和处理方案
|根因|典型表现|处理|
|---|----------|-------|
|连接数打满|Too many connections、应用连不上|降低连接池、加 RDS Proxy、排查连接泄漏|
|CPU 打满|CPU 90–100%、SQL 慢|查 Top SQL、加索引、优化 SQL、升配|
|内存不足|FreeableMemory 低、Swap 高|降连接数、调参数、升配|
|IO 瓶颈|Latency 高、DiskQueueDepth 高|优化 SQL、升 gp3 IOPS/吞吐、换 io2|
|存储满FreeStorageSpace 低、写入失败|扩容、清日志、开 autoscaling|
|慢 SQL / 锁|CPU 不一定高，但请求超时|查锁、慢日志、事务、索引|
|Multi-AZ failover|短暂不可用，事件有 failover|应用重试、DNS TTL、连接池健康检查|
|维护窗口影响|固定时间异常|调整 maintenance/backup window|
|应用重试风暴|故障后连接暴增|限流、指数退避、熔断|

