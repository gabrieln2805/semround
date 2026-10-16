WITH a0 AS (
  SELECT "customers"."region" AS "d0", SUM("order_items"."quantity") AS v
    FROM "shop"."main"."order_items" AS "order_items"
    LEFT JOIN "shop"."main"."orders" AS "orders" ON "order_items"."order_id" = "orders"."order_id"
    LEFT JOIN "shop"."main"."customers" AS "customers" ON "orders"."customer_id" = "customers"."customer_id"
  GROUP BY 1
),
a1 AS (
  SELECT "customers"."region" AS "d0", COUNT(DISTINCT "orders"."order_id") AS v
    FROM "shop"."main"."orders" AS "orders"
    LEFT JOIN "shop"."main"."customers" AS "customers" ON "orders"."customer_id" = "customers"."customer_id"
  GROUP BY 1
),
g AS (
  SELECT "d0" FROM a0
  UNION
  SELECT "d0" FROM a1
)
SELECT g."d0" AS "customers.region", a0.v / COALESCE(a1.v, 0) AS "units_per_order"
FROM g
LEFT JOIN a0 ON g."d0" IS NOT DISTINCT FROM a0."d0"
LEFT JOIN a1 ON g."d0" IS NOT DISTINCT FROM a1."d0"
ORDER BY 1 NULLS FIRST
