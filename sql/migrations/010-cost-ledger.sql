-- Migration 010: cost_ledger — rate-limiting y cost-guards por rol de subagente
-- Issue #91: Sin guardrails económicos, un subagente buggy puede consumir USD$$$
-- en tokens.
--
-- Diseño:
--   cost_ledger — cada fila = un sub-agente que terminó, con tokens y costo USD
--   Calculado al recibir el evento Finish del SSE de goosed
--   Enforced en spawn() chequeando SUM(usd_cost) WHERE role=? AND DATE=today
--     vs max_usd_per_day de policies.yaml
--
-- Pricing oficial DeepSeek V4 (2026-06):
--   deepseek-v4-flash: input=$0.14/1M (miss), $0.0028/1M (cache hit), output=$0.28/1M
--   deepseek-v4-pro:   input=$0.435/1M (miss), $0.003625/1M (cache hit), output=$0.87/1M

CREATE TABLE IF NOT EXISTS cost_ledger (
    id                BIGSERIAL    PRIMARY KEY,
    session_id        TEXT         NOT NULL REFERENCES agent_sessions(id) ON DELETE CASCADE,
    role              TEXT         NOT NULL,            -- dev / ops / study / research / ...
    model             TEXT         NOT NULL DEFAULT 'deepseek-v4-flash',
    -- Token counts reales (desde Finish event)
    prompt_tokens     INTEGER      NOT NULL DEFAULT 0,
    completion_tokens INTEGER     NOT NULL DEFAULT 0,
    -- Cache hit/miss desglose (si el Finish event lo provee)
    cache_hit_tokens  INTEGER      NOT NULL DEFAULT 0,
    cache_miss_tokens INTEGER     NOT NULL DEFAULT 0,
    -- Costo calculado en USD
    usd_cost          NUMERIC(12, 8) NOT NULL,
    recorded_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_cost_ledger_role_date
    ON cost_ledger (role, recorded_at DESC);

CREATE INDEX IF NOT EXISTS idx_cost_ledger_session
    ON cost_ledger (session_id);

CREATE INDEX IF NOT EXISTS idx_cost_ledger_recorded
    ON cost_ledger (recorded_at DESC);

COMMENT ON TABLE  cost_ledger IS
    'Registro de costos de sub-agentes para rate-limiting diario por rol (issue #91).';
COMMENT ON COLUMN cost_ledger.usd_cost IS
    'Costo calculado en USD usando precios DeepSeek V4 al momento de registro.';
COMMENT ON COLUMN cost_ledger.role IS
    'Denormalizado desde agent_sessions para queries rápidas de suma diaria.';

-- ─── Función: calcular costo USD desde token counts ──────────────────────────
-- Usa precios DeepSeek V4 oficiales (junio 2026).
-- Cache hit tokens se facturan al 2% del precio de cache miss.

CREATE OR REPLACE FUNCTION calculate_token_cost(
    p_model           TEXT,
    p_cache_hit_tokens  INTEGER DEFAULT 0,
    p_cache_miss_tokens INTEGER DEFAULT 0,
    p_completion_tokens INTEGER DEFAULT 0
) RETURNS NUMERIC(12, 8) AS $$
DECLARE
    v_input_hit_price   NUMERIC(10, 8);
    v_input_miss_price  NUMERIC(10, 8);
    v_output_price      NUMERIC(10, 8);
BEGIN
    -- Cargar precios según modelo
    CASE p_model
        WHEN 'deepseek-v4-flash' THEN
            v_input_hit_price  := 0.0000028;   -- $0.0028/1M
            v_input_miss_price := 0.00000014;  -- $0.14/1M
            v_output_price     := 0.00000028;  -- $0.28/1M
        WHEN 'deepseek-v4-pro' THEN
            v_input_hit_price  := 0.000003625;  -- $0.003625/1M
            v_input_miss_price := 0.000000435;  -- $0.435/1M
            v_output_price     := 0.00000087;   -- $0.87/1M
        ELSE
            -- Fallback: flash pricing (más conservador)
            v_input_hit_price  := 0.0000028;
            v_input_miss_price := 0.00000014;
            v_output_price     := 0.00000028;
    END CASE;

    RETURN ROUND(
        (p_cache_hit_tokens  * v_input_hit_price) +
        (p_cache_miss_tokens * v_input_miss_price) +
        (p_completion_tokens * v_output_price),
        8
    );
END;
$$ LANGUAGE plpgsql IMMUTABLE;

COMMENT ON FUNCTION calculate_token_cost IS
    'Calcula costo USD desde token counts usando precios DeepSeek V4 (issue #91).';

-- ─── Vista: consumo diario por rol ───────────────────────────────────────────
CREATE OR REPLACE VIEW cost_daily_by_role AS
SELECT
    role,
    DATE(recorded_at AT TIME ZONE 'UTC') AS day,
    COUNT(*)::INT                         AS agent_runs,
    COALESCE(SUM(prompt_tokens), 0)::BIGINT     AS total_prompt_tokens,
    COALESCE(SUM(completion_tokens), 0)::BIGINT AS total_completion_tokens,
    COALESCE(SUM(usd_cost), 0)::NUMERIC(12, 8)  AS total_usd_cost
FROM cost_ledger
GROUP BY role, DATE(recorded_at AT TIME ZONE 'UTC')
ORDER BY day DESC, role;

COMMENT ON VIEW cost_daily_by_role IS
    'Resumen de consumo diario de tokens y costo USD agrupado por rol (issue #91).';

-- ─── Función: ¿el rol excedió su presupuesto diario? ─────────────────────────
-- Retorna (exceeded bool, current_usd numeric, max_usd numeric).
-- Si max_usd_per_day = 0, nunca excede (sin límite).

CREATE OR REPLACE FUNCTION check_daily_budget(
    p_role         TEXT,
    p_max_usd_per_day NUMERIC(12, 2)
) RETURNS TABLE(
    exceeded     BOOLEAN,
    current_usd  NUMERIC(12, 8),
    max_usd      NUMERIC(12, 2)
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        COALESCE(SUM(cl.usd_cost), 0) > p_max_usd_per_day AS exceeded,
        COALESCE(SUM(cl.usd_cost), 0)::NUMERIC(12, 8) AS current_usd,
        p_max_usd_per_day AS max_usd
    FROM cost_ledger cl
    WHERE cl.role = p_role
      AND cl.recorded_at >= DATE_TRUNC('day', NOW() AT TIME ZONE 'UTC')
      AND cl.recorded_at < DATE_TRUNC('day', NOW() AT TIME ZONE 'UTC') + INTERVAL '1 day';
END;
$$ LANGUAGE plpgsql STABLE;

COMMENT ON FUNCTION check_daily_budget IS
    'Verifica si un rol excedió su presupuesto diario en USD (issue #91).';
