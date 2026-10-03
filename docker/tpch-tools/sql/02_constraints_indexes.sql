-- Keys, foreign keys and the baseline secondary-index manifest.
-- The six secondary indexes are identical to the pilot's schema so that the
-- poster smoke run and the pilot share one physical design. Any change here
-- changes the snapshot's index-manifest hash.

ALTER TABLE public.region   ADD CONSTRAINT region_pkey   PRIMARY KEY (r_regionkey);
ALTER TABLE public.nation   ADD CONSTRAINT nation_pkey   PRIMARY KEY (n_nationkey);
ALTER TABLE public.part     ADD CONSTRAINT part_pkey     PRIMARY KEY (p_partkey);
ALTER TABLE public.supplier ADD CONSTRAINT supplier_pkey PRIMARY KEY (s_suppkey);
ALTER TABLE public.partsupp ADD CONSTRAINT partsupp_pkey PRIMARY KEY (ps_partkey, ps_suppkey);
ALTER TABLE public.customer ADD CONSTRAINT customer_pkey PRIMARY KEY (c_custkey);
ALTER TABLE public.orders   ADD CONSTRAINT orders_pkey   PRIMARY KEY (o_orderkey);
ALTER TABLE public.lineitem ADD CONSTRAINT lineitem_pkey PRIMARY KEY (l_orderkey, l_linenumber);

ALTER TABLE public.nation   ADD CONSTRAINT nation_region_fk   FOREIGN KEY (n_regionkey) REFERENCES public.region (r_regionkey);
ALTER TABLE public.supplier ADD CONSTRAINT supplier_nation_fk FOREIGN KEY (s_nationkey) REFERENCES public.nation (n_nationkey);
ALTER TABLE public.partsupp ADD CONSTRAINT partsupp_part_fk   FOREIGN KEY (ps_partkey)  REFERENCES public.part (p_partkey);
ALTER TABLE public.partsupp ADD CONSTRAINT partsupp_supp_fk   FOREIGN KEY (ps_suppkey)  REFERENCES public.supplier (s_suppkey);
ALTER TABLE public.customer ADD CONSTRAINT customer_nation_fk FOREIGN KEY (c_nationkey) REFERENCES public.nation (n_nationkey);
ALTER TABLE public.orders   ADD CONSTRAINT orders_customer_fk FOREIGN KEY (o_custkey)   REFERENCES public.customer (c_custkey);
ALTER TABLE public.lineitem ADD CONSTRAINT lineitem_orders_fk FOREIGN KEY (l_orderkey)  REFERENCES public.orders (o_orderkey);
ALTER TABLE public.lineitem ADD CONSTRAINT lineitem_partsupp_fk FOREIGN KEY (l_partkey, l_suppkey)
    REFERENCES public.partsupp (ps_partkey, ps_suppkey);

CREATE INDEX idx_customer_nation    ON public.customer (c_nationkey);
CREATE INDEX idx_supplier_nation    ON public.supplier (s_nationkey);
CREATE INDEX idx_orders_customer    ON public.orders (o_custkey);
CREATE INDEX idx_orders_date        ON public.orders (o_orderdate);
CREATE INDEX idx_lineitem_part_supp ON public.lineitem (l_partkey, l_suppkey);
CREATE INDEX idx_lineitem_shipdate  ON public.lineitem (l_shipdate);
