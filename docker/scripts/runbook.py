from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class RunbookStep:
    id: str
    title: str
    priority: str
    risk: str
    auto_executable: bool
    command_type: str
    command: Optional[str]
    explanation: str
    verify: str
    rollback: str


def _types(findings) -> set[str]:
    return {str(finding.get("type", "")) for finding in findings}


def build_runbook(findings) -> list[RunbookStep]:
    finding_types = _types(findings)
    steps: list[RunbookStep] = []

    if "LOCK" in finding_types:
        steps.append(
            RunbookStep(
                id="pg-lock-chain-check",
                title="确认 PostgreSQL 锁阻塞链",
                priority="P0",
                risk="LOW",
                auto_executable=True,
                command_type="sql",
                command="""
SELECT
  blocked.pid AS blocked_pid,
  now() - blocked.query_start AS blocked_age,
  left(blocked.query, 1000) AS blocked_query,
  blocker.pid AS blocker_pid,
  now() - blocker.query_start AS blocker_age,
  left(blocker.query, 1000) AS blocker_query
FROM pg_stat_activity blocked
JOIN LATERAL unnest(pg_blocking_pids(blocked.pid)) AS bpid(pid) ON true
JOIN pg_stat_activity blocker ON blocker.pid = bpid.pid
ORDER BY blocked_age DESC;
""",
                explanation="只读 SQL，用于确认当前谁在阻塞谁，并定位最上游 blocker。",
                verify="重新查看 blocking_locks，确认阻塞链是否收敛，并核对应用错误率与响应时间。",
                rollback="只读查询，无需回滚。",
            )
        )
        steps.append(
            RunbookStep(
                id="pg-cancel-blocker",
                title="取消阻塞查询",
                priority="P0",
                risk="MEDIUM",
                auto_executable=False,
                command_type="sql_template",
                command="SELECT pg_cancel_backend(<blocking_pid>);",
                explanation="优先取消仍在执行中的 blocker 查询，风险低于 terminate。",
                verify="确认等待会话减少，事务与业务请求恢复推进。",
                rollback="无法回滚，若取消失败可能需要升级到 terminate。",
            )
        )

    if "TRANSACTION" in finding_types or "IDLE_IN_TRANSACTION" in finding_types:
        steps.append(
            RunbookStep(
                id="pg-long-xact-check",
                title="确认长事务和 idle in transaction 来源",
                priority="P0",
                risk="LOW",
                auto_executable=True,
                command_type="sql",
                command="""
SELECT
  pid,
  usename,
  application_name,
  client_addr,
  state,
  now() - xact_start AS xact_age,
  left(query, 1000) AS query
FROM pg_stat_activity
WHERE xact_start IS NOT NULL
ORDER BY xact_age DESC;
""",
                explanation="只读 SQL，用于定位长事务来源和可疑应用侧连接。",
                verify="结合 application_name、client_addr、state 判断是否为异常事务或未提交连接。",
                rollback="只读查询，无需回滚。",
            )
        )

    if "CONNECTION" in finding_types:
        steps.append(
            RunbookStep(
                id="pg-connection-distribution",
                title="确认连接来源分布",
                priority="P0",
                risk="LOW",
                auto_executable=True,
                command_type="sql",
                command="""
SELECT
  usename,
  application_name,
  client_addr,
  state,
  count(*) AS sessions
FROM pg_stat_activity
GROUP BY usename, application_name, client_addr, state
ORDER BY sessions DESC;
""",
                explanation="只读 SQL，用于判断是否为连接池配置过大、实例扩容过猛或客户端泄漏连接。",
                verify="确认高连接数是否集中在少数应用或节点上，并与 DatabaseConnections 趋势对照。",
                rollback="只读查询，无需回滚。",
            )
        )
        steps.append(
            RunbookStep(
                id="app-reduce-pool-size",
                title="下调应用连接池或实例并发",
                priority="P0",
                risk="MEDIUM",
                auto_executable=False,
                command_type="manual",
                command=None,
                explanation="减少连接池上限或临时缩小并发入口，避免数据库连接被打满。",
                verify="DatabaseConnections 回落，新建连接失败和连接超时告警下降。",
                rollback="可恢复连接池参数，但不要立刻恢复到事故前峰值。",
            )
        )

    if "CPU" in finding_types or "IO" in finding_types:
        steps.append(
            RunbookStep(
                id="pg-top-load-sql",
                title="定位高负载 SQL",
                priority="P1",
                risk="LOW",
                auto_executable=True,
                command_type="sql",
                command="""
SELECT
  calls,
  round(total_exec_time::numeric, 2) AS total_exec_time_ms,
  round(mean_exec_time::numeric, 2) AS mean_exec_time_ms,
  round(max_exec_time::numeric, 2) AS max_exec_time_ms,
  shared_blks_read,
  shared_blks_written,
  temp_blks_read,
  temp_blks_written,
  left(query, 1500) AS query
FROM pg_stat_statements
ORDER BY total_exec_time DESC
LIMIT 20;
""",
                explanation="只读 SQL，用于识别高 CPU 或高 IO 负载的热点语句。",
                verify="结合 PI top waits/top SQL，确认是否存在单条 SQL 或批处理任务主导负载。",
                rollback="只读查询，无需回滚。",
            )
        )

    if "STORAGE" in finding_types:
        steps.append(
            RunbookStep(
                id="rds-check-storage-growth",
                title="确认存储逼近耗尽的来源",
                priority="P0",
                risk="LOW",
                auto_executable=False,
                command_type="manual",
                command=None,
                explanation="检查大表增长、WAL 积压、临时文件和复制槽堆积，确认是否需要扩容存储或释放空间。",
                verify="FreeStorageSpace 回升，RDS 日志不再出现 No space left、storage full 等报错。",
                rollback="若执行了扩容无需回滚；若清理对象，需按业务侧确认恢复策略。",
            )
        )

    if "AVAILABILITY_EVENT" in finding_types or "MAINTENANCE" in finding_types:
        steps.append(
            RunbookStep(
                id="rds-check-events",
                title="核对 RDS 事件与维护窗口",
                priority="P0",
                risk="LOW",
                auto_executable=False,
                command_type="manual",
                command=None,
                explanation="优先确认故障是否由 failover、reboot、patch、upgrade、backup 或恢复过程触发。",
                verify="将事件时间线与应用报错时间、连接中断时间逐一对齐。",
                rollback="核查动作，无需回滚。",
            )
        )

    if "REPLICATION" in finding_types:
        steps.append(
            RunbookStep(
                id="rds-check-replication-lag",
                title="确认复制延迟与复制槽状态",
                priority="P1",
                risk="LOW",
                auto_executable=False,
                command_type="manual",
                command=None,
                explanation="检查只读实例延迟、复制槽积压和下游消费异常，避免 WAL 堆积进一步拖垮实例。",
                verify="ReplicaLag 回落，复制槽状态恢复正常。",
                rollback="核查动作，无需回滚。",
            )
        )

    if not steps:
        steps.append(
            RunbookStep(
                id="collect-more-evidence",
                title="补充证据采集",
                priority="P2",
                risk="LOW",
                auto_executable=False,
                command_type="manual",
                command=None,
                explanation="当前证据不足以给出明确处置动作，需要补充应用日志、慢 SQL、PI 和 RDS 日志。",
                verify="至少补齐同一时间窗内的应用错误日志、连接池配置和 RDS 事件。",
                rollback="仅采集信息，无需回滚。",
            )
        )

    return steps


def runbook_to_markdown(steps: list[RunbookStep]) -> str:
    lines: list[str] = []
    for index, step in enumerate(steps, 1):
        lines.append(f"### {index}. [{step.priority}] {step.title}")
        lines.append(f"- 风险: **{step.risk}**")
        lines.append(f"- 可自动执行: `{step.auto_executable}`")
        lines.append(f"- 类型: `{step.command_type}`")
        lines.append(f"- 说明: {step.explanation}")
        lines.append(f"- 验证: {step.verify}")
        lines.append(f"- 回滚/注意事项: {step.rollback}")
        if step.command:
            lang = "sql" if step.command_type.startswith("sql") else "bash"
            lines.append("")
            lines.append(f"```{lang}")
            lines.append(step.command.strip())
            lines.append("```")
        lines.append("")
    return "\n".join(lines)
