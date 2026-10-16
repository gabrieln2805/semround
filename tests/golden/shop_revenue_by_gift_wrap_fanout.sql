WITH a0 AS (
  SELECT __g."d0" AS "d0", SUM("orders"."amount") AS v
    FROM (SELECT DISTINCT "orders"."order_id" AS "k0", "order_items"."gift_wrap" AS "d0"
          FROM "shop"."main"."orders" AS "orders"
    LEFT JOIN "shop"."main"."order_items" AS "order_items" ON "order_items"."order_id" = "orders"."order_id") AS __g
    JOIN "shop"."main"."orders" AS "orders" ON "orders"."order_id" = __g."k0"
  GROUP BY 1
),
g AS (
  SELECT "d0" FROM a0
)
SELECT g."d0" AS "order_items.gift_wrap", a0.v AS "revenue"
FROM g
LEFT JOIN a0 ON g."d0" IS NOT DISTINCT FROM a0."d0"
ORDER BY 1 NULLS FIRST
