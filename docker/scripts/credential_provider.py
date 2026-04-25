import os
import boto3
import subprocess
import logging

log = logging.getLogger(__name__)


def get_db_password():
    """
    多级 fallback：

    1. IAM DB Auth
    2. SSM Parameter Store
    3. ENV (PGPASSWORD)
    """

    # ---------- 1️⃣ IAM DB Auth ----------
    if os.getenv("USE_IAM_AUTH", "false").lower() == "true":
        try:
            log.info("[AUTH] Trying IAM DB Auth")

            rds = boto3.client("rds")

            token = rds.generate_db_auth_token(
                DBHostname=os.environ["PGHOST_REAL"],
                Port=int(os.environ.get("PGPORT", "5432")),
                DBUsername=os.environ["PGUSER"],
                Region=os.environ["AWS_REGION"]
            )

            log.info("[AUTH] IAM token generated")

            return token

        except Exception as e:
            log.warning(f"[AUTH] IAM auth failed: {e}")

    # ---------- 2️⃣ SSM Parameter Store ----------
    param_name = os.getenv("DB_PASSWORD_PARAM")

    if param_name:
        try:
            log.info(f"[AUTH] Fetching from SSM: {param_name}")

            ssm = boto3.client("ssm")

            resp = ssm.get_parameter(
                Name=param_name,
                WithDecryption=True
            )

            return resp["Parameter"]["Value"]

        except Exception as e:
            log.warning(f"[AUTH] SSM fetch failed: {e}")

    # ---------- 3️⃣ ENV fallback ----------
    env_pwd = os.getenv("PGPASSWORD")

    if env_pwd:
        log.warning("[AUTH] Using ENV password (fallback)")
        return env_pwd

    raise RuntimeError("No database credential available")