# TPC-H data generator and loader for the poster evidence milestone.
#
# Builds a third-party copy of TPC dbgen pinned to the exact commit used by the
# pilot (passed as build arguments from .env) and verifies the checked-out
# commit. Results derived from this data are "derived from TPC-H", never TPC-H
# benchmark results. Confirm the TPC license terms before building.
FROM debian:bookworm-slim

ARG TPCH_DBGEN_REPO
ARG TPCH_DBGEN_COMMIT

RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential ca-certificates git postgresql-client \
 && rm -rf /var/lib/apt/lists/*

RUN test -n "$TPCH_DBGEN_REPO" \
 && test -n "$TPCH_DBGEN_COMMIT" \
 && git clone "$TPCH_DBGEN_REPO" /opt/tpch-dbgen \
 && git -C /opt/tpch-dbgen checkout --detach "$TPCH_DBGEN_COMMIT" \
 && test "$(git -C /opt/tpch-dbgen rev-parse HEAD)" = "$TPCH_DBGEN_COMMIT" \
 && make -C /opt/tpch-dbgen

COPY sql/ /opt/qcp/sql/
COPY load_tpch.sh /opt/qcp/load_tpch.sh
# Strip Windows line endings in case the files were edited on Windows.
RUN sed -i 's/\r$//' /opt/qcp/load_tpch.sh /opt/qcp/sql/*.sql \
 && chmod 0755 /opt/qcp/load_tpch.sh

ENV QCP_DBGEN_DIR=/opt/tpch-dbgen \
    QCP_SQL_DIR=/opt/qcp/sql

ENTRYPOINT ["/opt/qcp/load_tpch.sh"]
