-- Settle visibility maps, hint bits and planner statistics once, before any
-- measurement, then stop autovacuum/autoanalyze from changing statistics during
-- collection (ANALYZE samples randomly, so a re-analyze would silently create a
-- different snapshot). Each statement runs in autocommit mode (psql -f).

VACUUM (FREEZE, ANALYZE) public.region;
VACUUM (FREEZE, ANALYZE) public.nation;
VACUUM (FREEZE, ANALYZE) public.part;
VACUUM (FREEZE, ANALYZE) public.supplier;
VACUUM (FREEZE, ANALYZE) public.partsupp;
VACUUM (FREEZE, ANALYZE) public.customer;
VACUUM (FREEZE, ANALYZE) public.orders;
VACUUM (FREEZE, ANALYZE) public.lineitem;

ALTER TABLE public.region   SET (autovacuum_enabled = false);
ALTER TABLE public.nation   SET (autovacuum_enabled = false);
ALTER TABLE public.part     SET (autovacuum_enabled = false);
ALTER TABLE public.supplier SET (autovacuum_enabled = false);
ALTER TABLE public.partsupp SET (autovacuum_enabled = false);
ALTER TABLE public.customer SET (autovacuum_enabled = false);
ALTER TABLE public.orders   SET (autovacuum_enabled = false);
ALTER TABLE public.lineitem SET (autovacuum_enabled = false);

GRANT USAGE ON SCHEMA public TO qcp_bench_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO qcp_bench_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA qcp_meta TO qcp_bench_reader;
