-- TPC-H tables for the poster evidence milestone (run as qcp_bench_owner).
-- Column types follow the pilot schema; NOT NULL is added because TPC-H data
-- contains no NULLs (this matters for rewrite-equivalence arguments).
-- Keys and indexes are added after COPY in 02_constraints_indexes.sql.

CREATE SCHEMA IF NOT EXISTS qcp_meta;

CREATE TABLE IF NOT EXISTS qcp_meta.load_state (
    scale_factor text PRIMARY KEY,
    state        text NOT NULL CHECK (state IN ('loading', 'complete')),
    updated_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS qcp_meta.load_manifest (
    table_name   text PRIMARY KEY,
    row_count    bigint NOT NULL,
    sha256       text NOT NULL,
    scale_factor text NOT NULL,
    dbgen_repo   text NOT NULL,
    dbgen_commit text NOT NULL,
    loaded_at    timestamptz NOT NULL DEFAULT now()
);

GRANT USAGE ON SCHEMA qcp_meta TO qcp_bench_reader;
GRANT SELECT ON qcp_meta.load_state, qcp_meta.load_manifest TO qcp_bench_reader;

CREATE TABLE public.region (
    r_regionkey integer NOT NULL,
    r_name      char(25) NOT NULL,
    r_comment   varchar(152) NOT NULL
);

CREATE TABLE public.nation (
    n_nationkey integer NOT NULL,
    n_name      char(25) NOT NULL,
    n_regionkey integer NOT NULL,
    n_comment   varchar(152) NOT NULL
);

CREATE TABLE public.part (
    p_partkey     integer NOT NULL,
    p_name        varchar(55) NOT NULL,
    p_mfgr        char(25) NOT NULL,
    p_brand       char(10) NOT NULL,
    p_type        varchar(25) NOT NULL,
    p_size        integer NOT NULL,
    p_container   char(10) NOT NULL,
    p_retailprice numeric(15,2) NOT NULL,
    p_comment     varchar(23) NOT NULL
);

CREATE TABLE public.supplier (
    s_suppkey   integer NOT NULL,
    s_name      char(25) NOT NULL,
    s_address   varchar(40) NOT NULL,
    s_nationkey integer NOT NULL,
    s_phone     char(15) NOT NULL,
    s_acctbal   numeric(15,2) NOT NULL,
    s_comment   varchar(101) NOT NULL
);

CREATE TABLE public.partsupp (
    ps_partkey    integer NOT NULL,
    ps_suppkey    integer NOT NULL,
    ps_availqty   integer NOT NULL,
    ps_supplycost numeric(15,2) NOT NULL,
    ps_comment    varchar(199) NOT NULL
);

CREATE TABLE public.customer (
    c_custkey    integer NOT NULL,
    c_name       varchar(25) NOT NULL,
    c_address    varchar(40) NOT NULL,
    c_nationkey  integer NOT NULL,
    c_phone      char(15) NOT NULL,
    c_acctbal    numeric(15,2) NOT NULL,
    c_mktsegment char(10) NOT NULL,
    c_comment    varchar(117) NOT NULL
);

CREATE TABLE public.orders (
    o_orderkey      bigint NOT NULL,
    o_custkey       integer NOT NULL,
    o_orderstatus   char(1) NOT NULL,
    o_totalprice    numeric(15,2) NOT NULL,
    o_orderdate     date NOT NULL,
    o_orderpriority char(15) NOT NULL,
    o_clerk         char(15) NOT NULL,
    o_shippriority  integer NOT NULL,
    o_comment       varchar(79) NOT NULL
);

CREATE TABLE public.lineitem (
    l_orderkey      bigint NOT NULL,
    l_partkey       integer NOT NULL,
    l_suppkey       integer NOT NULL,
    l_linenumber    integer NOT NULL,
    l_quantity      numeric(15,2) NOT NULL,
    l_extendedprice numeric(15,2) NOT NULL,
    l_discount      numeric(15,2) NOT NULL,
    l_tax           numeric(15,2) NOT NULL,
    l_returnflag    char(1) NOT NULL,
    l_linestatus    char(1) NOT NULL,
    l_shipdate      date NOT NULL,
    l_commitdate    date NOT NULL,
    l_receiptdate   date NOT NULL,
    l_shipinstruct  char(25) NOT NULL,
    l_shipmode      char(10) NOT NULL,
    l_comment       varchar(44) NOT NULL
);
