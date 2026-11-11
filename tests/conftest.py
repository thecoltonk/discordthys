import os

# piccolo_conf reads DATABASE_URL at import time, so point it at the test DB
# before anything imports it.
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://cs:cs@localhost:5432/cs_test"
)
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
