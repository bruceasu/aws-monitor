#!/usr/bin/env python3
#-*- coding: utf-8 -*-
import json
import os
import re
import sys
from statistics import mean
out_dir, db_id, region, start_time, end_time = sys.argv[1:6]
def read_json(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}
def bytes_to_gib(v):
    if v is None:
        return None
    return v / (1024 ** 3)
def fmt(v, digits=2):
    if v is None:
        return "N/A"
    if isinstance(v, float):
        return f"{v:.{digits}f}"
    return str(v)
def metric_path(name):
    safe = name.replace("%", "_").replace("/", "_")
    return os.path.join(out_dir, "metrics", f"{safe}.json")
def get_points(metric_name):
    data = read_json(metric_path(metric_name), {})
    pts = data.get("Datapoints", [])
    return sorted(pts, key=lambda x: x.get("Timestamp", ""))
def values(metric_name, stat):
    pts = get_points(metric_name)
    vals = []
    for p in pts:
        if stat in p and p[stat] is not None:
            vals.append(float(p[stat]))
    return vals
def summarize_metric(metric_name, primary="Average", secondary="Maximum"):
    vals1 = values(metric_name, primary)
    vals2 = values(metric_name, secondary)
    if not vals1 and not vals2:
        return None
    result = {}
    if vals1:
        result[f"{primary.lower()}_avg"] = mean(vals1)
        result[f"{primary.lower()}_min"] = min(vals1)
        result[f"{primary.lower()}_max"] = max(vals1)
    if vals2:
        result[f"{secondary.lower()}_avg"] = mean(vals2)
        result[f"{secondary.lower()}_min"] = min(vals2)
        result[f"{secondary.lower()}_max"] = max(vals2)
    return result
db_data = read_json(os.path.join(out_dir, "db_instance.json"))
db = db_data.get("DBInstances", [{}])[0]
events_data = read_json(os.path.join(out_dir, "events.json"))
events = events_data.get("Events", [])
maintenance_data = read_json(os.path.join(out_dir, "pending_maintenance.json"))
pending = maintenance_data.get("PendingMaintenanceActions", [])
alarms_data = read_json(os.path.join(out_dir, "cloudwatch_alarms_all.json"))
alarms = alarms_data.get("MetricAlarms", [])
findings = []
solutions = []
def add_finding(severity, title, evidence, solution):
    findings.append({
        "severity": severity,
        "title": title,
        "evidence": evidence,
        "solution": solution,
    })
# Instance basics.
status = db.get("DBInstanceStatus")
engine = db.get("Engine")
engine_version = db.get("EngineVersion")
db_class = db.get("DBInstanceClass")
multi_az = db.get("MultiAZ")
storage_type = db.get("StorageType")
allocated_storage = db.get("AllocatedStorage")
max_allocated_storage = db.get("MaxAllocatedStorage")
auto_minor = db.get("AutoMinorVersionUpgrade")
backup_window = db.get("PreferredBackupWindow")
maintenance_window = db.get("PreferredMaintenanceWindow")
pi_enabled = db.get("PerformanceInsightsEnabled")
deletion_protection = db.get("DeletionProtection")
latest_restorable = db.get("LatestRestorableTime")
# Events analysis.
event_text = "\n".join(
    f"{e.get('Date')} {e.get('SourceIdentifier')} {e.get('Message')}"
    for e in events
)
lower_event = event_text.lower()
event_patterns = {
    "failover": ["failover", "multi-az"],
    "reboot": ["reboot", "restarted", "restart"],
    "crash": ["crash", "shutdown", "terminated"],
    "maintenance": ["maintenance", "patch", "upgrade"],
    "storage": ["storage", "free storage", "allocated storage"],
    "backup": ["backup", "snapshot"],
}
matched_events = {}
for key, pats in event_patterns.items():
    lines = []
    for line in event_text.splitlines():
        ll = line.lower()
        if any(p in ll for p in pats):
            lines.append(line)
    if lines:
        matched_events[key] = lines[:10]
if "failover" in matched_events:
    add_finding(
        "HIGH",
        "检测到 RDS failover / Multi-AZ 切换事件",
        "\n".join(matched_events["failover"][:5]),
        "检查 failover 前 10-30 分钟的 CPU、内存、IO、连接数和存储。应用侧必须支持连接重试、丢弃坏连接、DNS 不长期缓存；如果 failover 频繁，需要查底层压力、AZ 问题、维护事件或数据库进程异常。"
    )
if "reboot" in matched_events:
    add_finding(
        "HIGH",
        "检测到 RDS reboot / restart 事件",
        "\n".join(matched_events["reboot"][:5]),
        "确认是否有人手动重启、参数组变更需要重启、维护窗口触发，或数据库进程异常后被 RDS 自动恢复。继续核对同时间段错误日志和 CloudWatch 指标。"
    )
if "maintenance" in matched_events:
    add_finding(
        "MEDIUM",
        "检测到维护 / patch / upgrade 相关事件",
        "\n".join(matched_events["maintenance"][:5]),
        "将 maintenance window 放到业务低峰；关闭非必要 Auto minor version upgrade；参数组和版本升级走变更流程。"
    )
# CloudWatch metrics analysis.
cpu = summarize_metric("CPUUtilization", "Average", "Maximum")
if cpu:
    cpu_max = cpu.get("maximum_max")
    cpu_avg = cpu.get("average_avg")
    if cpu_max is not None and cpu_max >= 95:
        add_finding(
            "HIGH",
            "CPU 曾接近或达到满载",
            f"CPUUtilization Maximum max = {fmt(cpu_max)}%, Average avg = {fmt(cpu_avg)}%",
            "使用 Performance Insights 找 Top SQL / Top waits；优化高 CPU SQL、补索引、避免全表扫描；必要时临时升配。"
        )
    elif cpu_avg is not None and cpu_avg >= 80:
        add_finding(
            "MEDIUM",
            "CPU 持续偏高",
            f"CPUUtilization Average avg = {fmt(cpu_avg)}%",
            "持续 CPU 高通常不是 RDS 自身问题，而是 SQL、并发、实例规格或批处理任务导致。优先查 Top SQL。"
        )
conn = summarize_metric("DatabaseConnections", "Average", "Maximum")
if conn:
    conn_max = conn.get("maximum_max")
    conn_avg = conn.get("average_avg")
    if conn_max is not None and conn_avg is not None and conn_max > max(100, conn_avg * 2.5):
        add_finding(
            "HIGH",
            "连接数出现异常尖峰",
            f"DatabaseConnections Maximum max = {fmt(conn_max)}, Average avg = {fmt(conn_avg)}",
            "检查应用连接池数量：服务实例数 × maximumPoolSize 是否超过数据库承受能力；排查连接泄漏、重试风暴、ECS/Lambda 扩容；考虑 RDS Proxy。"
        )
    elif conn_max is not None and conn_max > 500:
        add_finding(
            "MEDIUM",
            "连接数较高",
            f"DatabaseConnections Maximum max = {fmt(conn_max)}",
            "建议核对数据库 max_connections 与应用连接池总量。RDS 指标无法直接判断是否到上限，需要结合数据库参数或错误日志。"
        )
mem = summarize_metric("FreeableMemory", "Average", "Minimum")
if mem:
    mem_min = mem.get("minimum_min")
    if mem_min is not None and mem_min < 512 * 1024 * 1024:
        add_finding(
            "HIGH",
            "可用内存严重不足",
            f"FreeableMemory Minimum min = {fmt(bytes_to_gib(mem_min))} GiB",
            "降低连接数，检查大查询、排序、临时表、buffer/cache 参数；如果 Swap 同时升高，应优先升配或降低内存压力。"
        )
    elif mem_min is not None and mem_min < 1024 * 1024 * 1024:
        add_finding(
            "MEDIUM",
            "可用内存偏低",
            f"FreeableMemory Minimum min = {fmt(bytes_to_gib(mem_min))} GiB",
            "持续观察 SwapUsage；检查连接数和内存相关参数。"
        )
swap = summarize_metric("SwapUsage", "Average", "Maximum")
if swap:
    swap_max = swap.get("maximum_max")
    if swap_max is not None and swap_max > 256 * 1024 * 1024:
        add_finding(
            "HIGH",
            "检测到明显 Swap 使用",
            f"SwapUsage Maximum max = {fmt(bytes_to_gib(swap_max))} GiB",
            "数据库使用 Swap 会显著恶化性能，并可能导致假死。建议减少连接数、优化大查询、升配内存。"
        )
storage = summarize_metric("FreeStorageSpace", "Average", "Minimum")
if storage:
    storage_min = storage.get("minimum_min")
    if storage_min is not None and storage_min < 5 * 1024 ** 3:
        add_finding(
            "CRITICAL",
            "剩余存储空间严重不足",
            f"FreeStorageSpace Minimum min = {fmt(bytes_to_gib(storage_min))} GiB",
            "立即扩容存储或清理日志/历史数据；开启 Storage Autoscaling；检查 binlog/WAL、临时表、批量写入。"
        )
    elif storage_min is not None and storage_min < 20 * 1024 ** 3:
        add_finding(
            "MEDIUM",
            "剩余存储空间偏低",
            f"FreeStorageSpace Minimum min = {fmt(bytes_to_gib(storage_min))} GiB",
            "设置 FreeStorageSpace 告警，规划扩容；如果增长很快，重点查 binlog/WAL、归档和大批量写入。"
        )
read_lat = summarize_metric("ReadLatency", "Average", "Maximum")
write_lat = summarize_metric("WriteLatency", "Average", "Maximum")
for name, m in [("ReadLatency", read_lat), ("WriteLatency", write_lat)]:
    if m:
        maxv = m.get("maximum_max")
        avgv = m.get("average_avg")
        if maxv is not None and maxv >= 0.2:
            add_finding(
                "HIGH",
                f"{name} 出现高延迟",
                f"{name} Maximum max = {fmt(maxv * 1000)} ms, Average avg = {fmt((avgv or 0) * 1000)} ms",
                "检查 IOPS/吞吐是否到顶、DiskQueueDepth 是否升高、是否有全表扫描/批量写入/大事务。gp3 可提高 IOPS 和吞吐；重负载场景考虑 io2。"
            )
        elif avgv is not None and avgv >= 0.05:
            add_finding(
                "MEDIUM",
                f"{name} 平均延迟偏高",
                f"{name} Average avg = {fmt(avgv * 1000)} ms",
                "结合 Performance Insights 的 IO waits 和 Top SQL 判断是否为 SQL 扫描或存储性能不足。"
            )
dq = summarize_metric("DiskQueueDepth", "Average", "Maximum")
if dq:
    dq_max = dq.get("maximum_max")
    dq_avg = dq.get("average_avg")
    if dq_max is not None and dq_max >= 20:
        add_finding(
            "HIGH",
            "磁盘队列深度较高",
            f"DiskQueueDepth Maximum max = {fmt(dq_max)}, Average avg = {fmt(dq_avg)}",
            "IO 请求排队会导致数据库响应慢甚至假死。检查慢 SQL、批处理、存储 IOPS/吞吐配置。"
        )
for metric in ["EBSIOBalance%", "EBSByteBalance%", "BurstBalance"]:
    m = summarize_metric(metric, "Average", "Minimum")
    if m:
        minv = m.get("minimum_min")
        if minv is not None and minv < 20:
            add_finding(
                "HIGH",
                f"{metric} 余额低",
                f"{metric} Minimum min = {fmt(minv)}%",
                "实例或存储突发余额耗尽会导致性能下降。考虑改用 gp3 并配置固定 IOPS/吞吐，或调整实例/存储规格。"
            )
credit = summarize_metric("CPUCreditBalance", "Average", "Minimum")
if credit:
    minv = credit.get("minimum_min")
    if minv is not None and minv < 20:
        add_finding(
            "MEDIUM",
            "CPU Credit 较低",
            f"CPUCreditBalance Minimum min = {fmt(minv)}",
            "如果是 t 系列 burstable 实例，CPU credit 耗尽会导致性能受限。生产数据库建议避免长期依赖 t 系列，或改用 m/r 系列。"
        )
deadlocks = summarize_metric("Deadlocks", "Sum", "Maximum")
if deadlocks:
    total = deadlocks.get("sum_avg")
    mx = deadlocks.get("maximum_max")
    if mx is not None and mx > 0:
        add_finding(
            "MEDIUM",
            "检测到 Deadlocks",
            f"Deadlocks Maximum max = {fmt(mx)}, Sum avg per period = {fmt(total)}",
            "排查事务顺序、索引、更新范围和长事务；开启并分析数据库死锁日志。"
        )
# Pending maintenance.
if pending:
    add_finding(
        "MEDIUM",
        "存在 pending maintenance actions",
        json.dumps(pending[:3], ensure_ascii=False, indent=2),
        "检查是否有需要重启的维护项；将维护安排到低峰，并明确变更窗口。"
    )
# Log scan.
log_findings = []
log_dir = os.path.join(out_dir, "logs")
patterns = [
    ("Too many connections", "连接数达到上限"),
    ("remaining connection slots are reserved", "PostgreSQL 连接槽不足"),
    ("out of memory", "数据库或进程内存不足"),
    ("No space left on device", "存储空间不足"),
    ("deadlock", "死锁"),
    ("Lock wait timeout", "锁等待超时"),
    ("crash", "数据库进程崩溃"),
    ("shutdown", "数据库关闭"),
    ("restart", "数据库重启"),
    ("aborted connection", "连接异常中断"),
]
if os.path.isdir(log_dir):
    for fn in os.listdir(log_dir):
        path = os.path.join(log_dir, fn)
        try:
            text = open(path, errors="ignore").read()
        except Exception:
            continue
        for pat, desc in patterns:
            if re.search(pat, text, re.IGNORECASE):
                sample = []
                for line in text.splitlines():
                    if re.search(pat, line, re.IGNORECASE):
                        sample.append(line[:500])
                        if len(sample) >= 3:
                            break
                log_findings.append((desc, fn, "\n".join(sample)))
for desc, fn, sample in log_findings[:10]:
    add_finding(
        "HIGH" if desc in ["连接数达到上限", "PostgreSQL 连接槽不足", "数据库或进程内存不足", "存储空间不足"] else "MEDIUM",
        f"日志中发现：{desc}",
        f"file={fn}\n{sample}",
        "结合日志时间点与 CloudWatch 指标定位触发原因。若是连接数问题，优先治理连接池和重试；若是 OOM/存储/锁，按对应方向处理。"
    )
# Performance Insights summaries.
pi_sql = read_json(os.path.join(out_dir, "perf_insights_top_sql.json"), {})
pi_waits = read_json(os.path.join(out_dir, "perf_insights_top_waits.json"), {})
if pi_waits.get("Keys"):
    add_finding(
        "INFO",
        "Performance Insights Top Waits 已采集",
        "见 perf_insights_top_waits.json",
        "如果 Top Waits 主要是 CPU，优先查 SQL 和索引；如果是 IO，查扫表/存储；如果是 lock，查长事务和锁等待。"
    )
if pi_sql.get("Keys"):
    add_finding(
        "INFO",
        "Performance Insights Top SQL 已采集",
        "见 perf_insights_top_sql.json",
        "优先优化 DB Load 占比最高的 SQL；必要时抓 EXPLAIN / EXPLAIN ANALYZE。"
    )
# Prioritize.
order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "INFO": 3}
findings = sorted(findings, key=lambda x: order.get(x["severity"], 9))
if not findings:
    add_finding(
        "INFO",
        "未从 AWS 层指标中发现明确异常",
        "RDS Events / CloudWatch 指标未触发脚本内置阈值。",
        "下一步应进入数据库内部排查：慢 SQL、锁、长事务、连接池、应用日志。也建议扩大 --hours 窗口或对准真实故障时间。"
    )
report_path = os.path.join(out_dir, "report.md")
with open(report_path, "w") as r:
    r.write(f"# RDS Diagnostic Report: {db_id}\n\n")
    r.write(f"- Region: `{region}`\n")
    r.write(f"- Window UTC: `{start_time}` to `{end_time}`\n")
    r.write(f"- DB status: `{status}`\n")
    r.write(f"- Engine: `{engine}` `{engine_version}`\n")
    r.write(f"- Class: `{db_class}`\n")
    r.write(f"- Multi-AZ: `{multi_az}`\n")
    r.write(f"- Storage: `{storage_type}`, allocated `{allocated_storage}` GiB, max autoscaling `{max_allocated_storage}` GiB\n")
    r.write(f"- Performance Insights: `{pi_enabled}`\n")
    r.write(f"- Backup window: `{backup_window}`\n")
    r.write(f"- Maintenance window: `{maintenance_window}`\n")
    r.write(f"- Auto minor version upgrade: `{auto_minor}`\n")
    r.write(f"- Latest restorable time: `{latest_restorable}`\n")
    r.write("\n---\n\n")
    r.write("## Executive Summary\n\n")
    crit_high = [f for f in findings if f["severity"] in ("CRITICAL", "HIGH")]
    if crit_high:
        r.write("脚本检测到高风险异常，优先处理以下问题：\n\n")
        for f in crit_high[:5]:
            r.write(f"- **[{f['severity']}] {f['title']}**\n")
    else:
        r.write("未检测到 CRITICAL/HIGH 级别的 AWS 层异常；建议进一步结合数据库内部状态和应用日志排查。\n")
    r.write("\n---\n\n")
    r.write("## Findings\n\n")
    for i, f in enumerate(findings, 1):
        r.write(f"### {i}. [{f['severity']}] {f['title']}\n\n")
        r.write("**Evidence**\n\n")
        r.write("```text\n")
        r.write(str(f["evidence"])[:4000])
        r.write("\n```\n\n")
        r.write("**Recommended action**\n\n")
        r.write(f"{f['solution']}\n\n")
    r.write("---\n\n")
    r.write("## Recent RDS Events\n\n")
    if events:
        for e in events[:50]:
            r.write(f"- `{e.get('Date')}` {e.get('Message')}\n")
    else:
        r.write("No events collected.\n")
    r.write("\n---\n\n")
    r.write("## Collected Files\n\n")
    r.write("- `db_instance.json`\n")
    r.write("- `events.json`\n")
    r.write("- `pending_maintenance.json`\n")
    r.write("- `cloudwatch_alarms_all.json`\n")
    r.write("- `metrics/*.json`\n")
    r.write("- `logs/*.txt`\n")
    if pi_enabled:
        r.write("- `perf_insights_top_sql.json`\n")
        r.write("- `perf_insights_top_waits.json`\n")
    print(report_path)