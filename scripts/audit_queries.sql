-- audit_queries.sql
-- 용도: 80_implementation_audit.md 성과표 재현을 위한 표준 집계 SQL
-- 대상 DB: trading.db (SQLite)
-- 기간: 2026-02-25 ~ 2026-02-28 (UTC)
-- 참조: docs/ouroboros/80_implementation_audit.md Section 3

------------------------------------------------------------------------
-- Base: 기간 + LIVE + SELL + 직전 BUY 메타 매칭
------------------------------------------------------------------------
-- 모든 후속 쿼리의 기반이 되는 CTE.
-- prev_buy_rationale: 직전 BUY의 rationale (startup-sync 분류용)
-- prev_buy_qty: 직전 BUY 수량 (수량 일치 무결성 필터용)
------------------------------------------------------------------------

WITH base AS (
  SELECT *
  FROM trades
  WHERE mode='live'
    AND action='SELL'
    AND timestamp >= '2026-02-25T00:00:00+00:00'
    AND timestamp <  '2026-02-28T00:00:00+00:00'
),
labeled AS (
  SELECT
    s.id,
    s.timestamp,
    s.stock_code,
    s.market,
    s.exchange_code,
    s.quantity AS sell_qty,
    s.price AS sell_price,
    s.pnl,
    COALESCE((
      SELECT b.rationale
      FROM trades b
      WHERE b.mode='live'
        AND b.action='BUY'
        AND b.stock_code=s.stock_code
        AND b.market=s.market
        AND b.timestamp < s.timestamp
      ORDER BY b.timestamp DESC, b.id DESC
      LIMIT 1
    ), '') AS prev_buy_rationale,
    (
      SELECT b.quantity
      FROM trades b
      WHERE b.mode='live'
        AND b.action='BUY'
        AND b.stock_code=s.stock_code
        AND b.market=s.market
        AND b.timestamp < s.timestamp
      ORDER BY b.timestamp DESC, b.id DESC
      LIMIT 1
    ) AS prev_buy_qty
  FROM base s
)
SELECT * FROM labeled;

------------------------------------------------------------------------
-- Q1) 통화 분리 손익 (KRW/USD 혼합 금지)
------------------------------------------------------------------------

WITH base AS (
  SELECT * FROM trades
  WHERE mode='live' AND action='SELL'
    AND timestamp >= '2026-02-25T00:00:00+00:00'
    AND timestamp <  '2026-02-28T00:00:00+00:00'
),
labeled AS (
  SELECT s.*,
         s.quantity AS sell_qty,
         COALESCE((SELECT b.rationale FROM trades b
                   WHERE b.mode='live' AND b.action='BUY'
                     AND b.stock_code=s.stock_code AND b.market=s.market
                     AND b.timestamp < s.timestamp
                   ORDER BY b.timestamp DESC, b.id DESC LIMIT 1), '') AS prev_buy_rationale,
         (SELECT b.quantity FROM trades b
          WHERE b.mode='live' AND b.action='BUY'
            AND b.stock_code=s.stock_code AND b.market=s.market
            AND b.timestamp < s.timestamp
          ORDER BY b.timestamp DESC, b.id DESC LIMIT 1) AS prev_buy_qty
  FROM base s
)
SELECT
  CASE WHEN market='KR' THEN 'KRW' ELSE 'USD' END AS ccy,
  COUNT(*) AS sells,
  ROUND(SUM(pnl),2) AS pnl_sum
FROM labeled
GROUP BY ccy
ORDER BY ccy;

------------------------------------------------------------------------
-- Q2) 기존 보유(startup-sync) 제외 성과
------------------------------------------------------------------------

WITH base AS (
  SELECT * FROM trades
  WHERE mode='live' AND action='SELL'
    AND timestamp >= '2026-02-25T00:00:00+00:00'
    AND timestamp <  '2026-02-28T00:00:00+00:00'
),
labeled AS (
  SELECT s.*,
         s.quantity AS sell_qty,
         COALESCE((SELECT b.rationale FROM trades b
                   WHERE b.mode='live' AND b.action='BUY'
                     AND b.stock_code=s.stock_code AND b.market=s.market
                     AND b.timestamp < s.timestamp
                   ORDER BY b.timestamp DESC, b.id DESC LIMIT 1), '') AS prev_buy_rationale,
         (SELECT b.quantity FROM trades b
          WHERE b.mode='live' AND b.action='BUY'
            AND b.stock_code=s.stock_code AND b.market=s.market
            AND b.timestamp < s.timestamp
          ORDER BY b.timestamp DESC, b.id DESC LIMIT 1) AS prev_buy_qty
  FROM base s
)
SELECT
  CASE WHEN market='KR' THEN 'KRW' ELSE 'USD' END AS ccy,
  COUNT(*) AS sells,
  ROUND(SUM(pnl),2) AS pnl_sum
FROM labeled
WHERE prev_buy_rationale NOT LIKE '[startup-sync]%'
GROUP BY ccy
ORDER BY ccy;

------------------------------------------------------------------------
-- Q3) 수량 일치 체결만 포함 (무결성 필터)
------------------------------------------------------------------------

WITH base AS (
  SELECT * FROM trades
  WHERE mode='live' AND action='SELL'
    AND timestamp >= '2026-02-25T00:00:00+00:00'
    AND timestamp <  '2026-02-28T00:00:00+00:00'
),
labeled AS (
  SELECT s.*,
         s.quantity AS sell_qty,
         COALESCE((SELECT b.rationale FROM trades b
                   WHERE b.mode='live' AND b.action='BUY'
                     AND b.stock_code=s.stock_code AND b.market=s.market
                     AND b.timestamp < s.timestamp
                   ORDER BY b.timestamp DESC, b.id DESC LIMIT 1), '') AS prev_buy_rationale,
         (SELECT b.quantity FROM trades b
          WHERE b.mode='live' AND b.action='BUY'
            AND b.stock_code=s.stock_code AND b.market=s.market
            AND b.timestamp < s.timestamp
          ORDER BY b.timestamp DESC, b.id DESC LIMIT 1) AS prev_buy_qty
  FROM base s
)
SELECT
  CASE WHEN market='KR' THEN 'KRW' ELSE 'USD' END AS ccy,
  COUNT(*) AS sells,
  ROUND(SUM(pnl),2) AS pnl_sum
FROM labeled
WHERE prev_buy_qty = sell_qty
GROUP BY ccy
ORDER BY ccy;

------------------------------------------------------------------------
-- Q4) 이상치 목록 (수량 불일치)
------------------------------------------------------------------------

WITH base AS (
  SELECT * FROM trades
  WHERE mode='live' AND action='SELL'
    AND timestamp >= '2026-02-25T00:00:00+00:00'
    AND timestamp <  '2026-02-28T00:00:00+00:00'
),
labeled AS (
  SELECT s.id, s.timestamp, s.stock_code, s.market, s.quantity AS sell_qty, s.pnl,
         (SELECT b.quantity FROM trades b
          WHERE b.mode='live' AND b.action='BUY'
            AND b.stock_code=s.stock_code AND b.market=s.market
            AND b.timestamp < s.timestamp
          ORDER BY b.timestamp DESC, b.id DESC LIMIT 1) AS prev_buy_qty
  FROM base s
)
SELECT
  id, timestamp, stock_code, market, sell_qty, prev_buy_qty, ROUND(pnl,2) AS pnl
FROM labeled
WHERE prev_buy_qty IS NOT NULL
  AND prev_buy_qty != sell_qty
ORDER BY ABS(pnl) DESC;
