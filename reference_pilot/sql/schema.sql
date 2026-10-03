
CREATE TABLE IF NOT EXISTS region (r_regionkey integer PRIMARY KEY, r_name char(25), r_comment varchar(152));
CREATE TABLE IF NOT EXISTS nation (n_nationkey integer PRIMARY KEY, n_name char(25), n_regionkey integer REFERENCES region, n_comment varchar(152));
CREATE TABLE IF NOT EXISTS part (p_partkey integer PRIMARY KEY, p_name varchar(55), p_mfgr char(25), p_brand char(10), p_type varchar(25), p_size integer, p_container char(10), p_retailprice numeric(15,2), p_comment varchar(23));
CREATE TABLE IF NOT EXISTS supplier (s_suppkey integer PRIMARY KEY, s_name char(25), s_address varchar(40), s_nationkey integer REFERENCES nation, s_phone char(15), s_acctbal numeric(15,2), s_comment varchar(101));
CREATE TABLE IF NOT EXISTS partsupp (ps_partkey integer REFERENCES part, ps_suppkey integer REFERENCES supplier, ps_availqty integer, ps_supplycost numeric(15,2), ps_comment varchar(199), PRIMARY KEY (ps_partkey, ps_suppkey));
CREATE TABLE IF NOT EXISTS customer (c_custkey integer PRIMARY KEY, c_name varchar(25), c_address varchar(40), c_nationkey integer REFERENCES nation, c_phone char(15), c_acctbal numeric(15,2), c_mktsegment char(10), c_comment varchar(117));
CREATE TABLE IF NOT EXISTS orders (o_orderkey bigint PRIMARY KEY, o_custkey integer REFERENCES customer, o_orderstatus char(1), o_totalprice numeric(15,2), o_orderdate date, o_orderpriority char(15), o_clerk char(15), o_shippriority integer, o_comment varchar(79));
CREATE TABLE IF NOT EXISTS lineitem (l_orderkey bigint REFERENCES orders, l_partkey integer, l_suppkey integer, l_linenumber integer, l_quantity numeric(15,2), l_extendedprice numeric(15,2), l_discount numeric(15,2), l_tax numeric(15,2), l_returnflag char(1), l_linestatus char(1), l_shipdate date, l_commitdate date, l_receiptdate date, l_shipinstruct char(25), l_shipmode char(10), l_comment varchar(44), PRIMARY KEY (l_orderkey, l_linenumber), FOREIGN KEY (l_partkey, l_suppkey) REFERENCES partsupp);

CREATE INDEX IF NOT EXISTS idx_customer_nation ON customer(c_nationkey);
CREATE INDEX IF NOT EXISTS idx_supplier_nation ON supplier(s_nationkey);
CREATE INDEX IF NOT EXISTS idx_orders_customer ON orders(o_custkey);
CREATE INDEX IF NOT EXISTS idx_orders_date ON orders(o_orderdate);
CREATE INDEX IF NOT EXISTS idx_lineitem_part_supp ON lineitem(l_partkey, l_suppkey);
CREATE INDEX IF NOT EXISTS idx_lineitem_shipdate ON lineitem(l_shipdate);
