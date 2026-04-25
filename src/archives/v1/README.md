# RDS诊断脚本
该脚本用于诊断AWS RDS实例的性能问题，收集相关指标和日志，并生成诊断报告。

# 01_rds_diag.sh
## 赋权：
> chmod +x 01_rds_diag.sh
## 运行：
```bash
./01_rds_diag.sh \
  --db my-rds-instance-id \
  --region ap-northeast-1 \
  --hours 24
```

## 输出目录类似：
> rds_diag_my-rds-instance-id_20260425_153000/
## 最终报告：
> rds_diag_my-rds-instance-id_20260425_153000/report.md

# 脚本会基于以下规则自动生成结论：
|检查项|可能判断出的根因|
|-----|--------------|
|RDS Events 出现 failover|Multi-AZ 切换、底层故障、实例压力、维护|
|RDS Events 出现 reboot/restart|手动重启、参数变更、维护、数据库进程异常|
|CPUUtilization| > 95%SQL 打满 CPU、实例规格不足、并发过高|
|DatabaseConnections 异常尖峰|连接池过大、连接泄漏、重试风暴、ECS/Lambda 扩容|
|FreeableMemory < 512 MiB|内存不足、连接过多、大查询、参数配置过大|
|SwapUsage > 256 MiB|内存压力严重，数据库可能假死|
|FreeStorageSpace < 5 GiB|存储空间不足，可能导致数据库不可写或异常|
|Read/WriteLatency 高|IO 瓶颈、全表扫描、大事务、存储规格不足|
|DiskQueueDepth 高|磁盘请求排队，数据库响应变慢|
|EBSIOBalance / EBSByteBalance 低|突发 IO/吞吐余额耗尽|
|CPUCreditBalance 低|t 系列实例 CPU credit 不足|
|日志出现 Too many connections|连接数达到上限|
|日志出现 out of memory|数据库进程内存不足|
|日志出现 deadlock / Lock wait timeout|死锁或锁等待|
|Performance Insights Top SQL|找出导致 DB Load 的 SQL|
|Performance Insights Top Waits|判断瓶颈是 CPU、IO、锁还是连接|


运行脚本的 IAM Role/User 至少需要：
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "rds:DescribeDBInstances",
        "rds:DescribeEvents",
        "rds:DescribePendingMaintenanceActions",
        "rds:DescribeDBLogFiles",
        "rds:DownloadDBLogFilePortion",
        "cloudwatch:GetMetricStatistics",
        "cloudwatch:DescribeAlarms",
        "pi:DescribeDimensionKeys",
        "pi:GetResourceMetrics"
      ],
      "Resource": "*"
    }
  ]
}
```
