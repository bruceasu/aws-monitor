需要的是只读诊断权限，不需要 rds:RebootDBInstance、rds:ModifyDBInstance 这类变更权限。现在的 AWS 采集脚本主要访问 RDS、CloudWatch、Performance Insights 三类 API。
下面是建议的最小权限策略。

# 1. 推荐 IAM Policy：只读采集版
把其中的：
- `REGION`
- `ACCOUNT_ID`
- `DB_INSTANCE_ID`

替换成的实际值。
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "RDSInstanceReadOnlyDiagnostics",
      "Effect": "Allow",
      "Action": [
        "rds:DescribeDBInstances",
        "rds:DescribeEvents",
        "rds:DescribePendingMaintenanceActions",
        "rds:DescribeDBLogFiles",
        "rds:DownloadDBLogFilePortion"
      ],
      "Resource": "*"
    },
    {
      "Sid": "CloudWatchReadOnlyDiagnostics",
      "Effect": "Allow",
      "Action": [
        "cloudwatch:GetMetricStatistics",
        "cloudwatch:GetMetricData",
        "cloudwatch:DescribeAlarms"
      ],
      "Resource": "*"
    },
    {
      "Sid": "PerformanceInsightsReadOnlyDiagnostics",
      "Effect": "Allow",
      "Action": [
        "pi:DescribeDimensionKeys",
        "pi:GetResourceMetrics"
      ],
      "Resource": "*"
    }
  ]
}
```
说明：
- rds:DescribeDBInstances：获取 RDS 实例规格、引擎、Multi-AZ、Performance Insights 是否启用等信息。AWS 官方 RDS IAM action 列表以 rds: action 控制这些 API。(AWS Documentation)
- rds:DescribeEvents：获取重启、failover、维护、存储等 RDS 事件。
- rds:DescribeDBLogFiles / rds:DownloadDBLogFilePortion：读取 RDS PostgreSQL 日志。
- cloudwatch:GetMetricStatistics / cloudwatch:GetMetricData：读取 CPU、连接数、内存、IO、延迟等 CloudWatch 指标。
- cloudwatch:DescribeAlarms：读取相关告警状态。
- pi:DescribeDimensionKeys / pi:GetResourceMetrics：读取 Performance Insights 的 Top SQL、Top Waits 等。AWS 托管策略 AmazonRDSPerformanceInsightsReadOnly 也包含 RDS describe 和 PI API 读取能力。(AWS 文档)

# 2. 更宽松的临时方案
如果只是临时排查，而且账号管理允许，可以临时挂这两个 AWS managed policy：
- AmazonRDSReadOnlyAccess
- CloudWatchReadOnlyAccess
- AmazonRDSPerformanceInsightsReadOnly

优点是省事。缺点是权限范围比单实例诊断大。AWS 官方也说明 managed policies 适合快速开始，但最小权限通常需要自定义 policy。(AWS Documentation)

# 3. 是否能限制到单个 RDS 实例？
部分 RDS Describe* API 对资源级权限支持有限，实践中经常需要：
- "Resource": "*"
尤其是：
- rds:DescribeDBInstances
- rds:DescribeEvents
- rds:DescribePendingMaintenanceActions
- rds:DescribeDBLogFiles

如果安全要求严格，可以用 Condition 辅助限制，例如按 region、账号、标签控制。但不要强行把所有 Describe* 都限制到某个 DB ARN，否则可能导致脚本报 AccessDenied。

# 4. 明确不需要的权限
诊断采集不需要这些高危权限：
- rds:RebootDBInstance
- rds:ModifyDBInstance
- rds:DeleteDBInstance
- rds:FailoverDBCluster
- rds:StartDBInstance
- rds:StopDBInstance
- rds:CreateDBSnapshot
- rds:RestoreDBInstanceFromDBSnapshot
也不需要：
- secretsmanager:GetSecretValue
- ssm:GetParameter
- kms:Decrypt
除非想让脚本自动读取数据库连接密码。

# 5. 如果还要采集 PostgreSQL 内部信息
AWS IAM 权限不够，还需要 PostgreSQL 数据库账号权限。
建议建一个只读诊断用户：
```sql
CREATE USER rds_diag_user WITH PASSWORD 'change_me_strong_password';
GRANT CONNECT ON DATABASE your_db TO rds_diag_user;
GRANT pg_monitor TO rds_diag_user;
```
pg_monitor 可以读取很多诊断视图，比如 pg_stat_activity、pg_locks、pg_stat_database 等。否则可能只能看到自己会话的信息，看不到其他用户的 SQL、锁和等待。
如果要读取 pg_stat_statements：
```sql
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
GRANT SELECT ON pg_stat_statements TO rds_diag_user;
```
不过在 RDS PostgreSQL 上启用 pg_stat_statements 通常还需要参数组里配置：
> shared_preload_libraries = 'pg_stat_statements'
这个参数变更通常需要重启数据库。

# 6. 最小权限总结
## AWS IAM
需要：
- rds:DescribeDBInstances
- rds:DescribeEvents
- rds:DescribePendingMaintenanceActions
- rds:DescribeDBLogFiles
- rds:DownloadDBLogFilePortion
- cloudwatch:GetMetricStatistics
- cloudwatch:GetMetricData
- cloudwatch:DescribeAlarms
- pi:DescribeDimensionKeys
- pi:GetResourceMetrics

## PostgreSQL DB 权限
建议：
> GRANT pg_monitor TO rds_diag_user;
可选：
> GRANT SELECT ON pg_stat_statements TO rds_diag_user;

如果公司安全策略比较严格，建议创建一个专用 IAM Role，例如：
> RDSIncidentDiagnosticsReadOnlyRole
只在事故排查期间由 SRE 临时 assume role 使用，并开启 CloudTrail 审计。