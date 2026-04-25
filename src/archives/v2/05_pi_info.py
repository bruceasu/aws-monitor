import json
import os
import sys
out_dir = sys.argv[1]
data = json.load(open(os.path.join(out_dir, "db_instance.json")))
db = data["DBInstances"][0]
print(f"DBI_RESOURCE_ID={db.get('DbiResourceId', '')}")
print(f"PERF_INSIGHTS_ENABLED={str(db.get('PerformanceInsightsEnabled', False)).lower()}")