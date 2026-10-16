WITH a0 AS (
  SELECT "store"."s_state" AS "d0", date_trunc('week', "date_dim"."d_date") AS "d1", SUM("store_sales"."ss_ext_sales_price") AS v
    FROM "tpcds"."public"."store_sales" AS "store_sales"
    LEFT JOIN "tpcds"."public"."store" AS "store" ON "store_sales"."ss_store_sk" = "store"."s_store_sk"
    LEFT JOIN "tpcds"."public"."date_dim" AS "date_dim" ON "store_sales"."ss_sold_date_sk" = "date_dim"."d_date_sk"
  GROUP BY 1, 2
),
g AS (
  SELECT "d0", "d1" FROM a0
)
SELECT g."d0" AS "store.s_state", g."d1" AS "date_dim.d_date__week", a0.v AS "total_sales"
FROM g
LEFT JOIN a0 ON g."d0" IS NOT DISTINCT FROM a0."d0" AND g."d1" IS NOT DISTINCT FROM a0."d1"
ORDER BY 1 NULLS FIRST, 2 NULLS FIRST
