WITH a0 AS (
  SELECT "store"."s_state" AS "d0", SUM("store_sales"."ss_ext_sales_price") AS v
    FROM "tpcds"."public"."store_sales" AS "store_sales"
    LEFT JOIN "tpcds"."public"."store" AS "store" ON "store_sales"."ss_store_sk" = "store"."s_store_sk"
  GROUP BY 1
),
a1 AS (
  SELECT "store"."s_state" AS "d0", SUM("store"."s_number_employees") AS v
    FROM "tpcds"."public"."store" AS "store"
  GROUP BY 1
),
g AS (
  SELECT "d0" FROM a0
  UNION
  SELECT "d0" FROM a1
)
SELECT g."d0" AS "store.s_state", a0.v / NULLIF(a1.v, 0) AS "store_productivity"
FROM g
LEFT JOIN a0 ON g."d0" IS NOT DISTINCT FROM a0."d0"
LEFT JOIN a1 ON g."d0" IS NOT DISTINCT FROM a1."d0"
ORDER BY 1 NULLS FIRST
