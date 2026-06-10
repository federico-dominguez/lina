-- ============================================================
-- LINA Rueda de la Vida — tabla de evaluaciones semanales
-- ============================================================
-- 8 áreas vitales evaluadas de 1 a 10.
-- La columna week_start se genera automáticamente a partir
-- de evaluated_at (DATE_TRUNC('week', evaluated_at)).

CREATE TABLE IF NOT EXISTS lina.wheel_of_life (
    id              BIGSERIAL PRIMARY KEY,
    area            VARCHAR(50) NOT NULL,
    score           INTEGER NOT NULL CHECK (score >= 1 AND score <= 10),
    notes           TEXT,
    evaluated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    week_start      DATE GENERATED ALWAYS AS (
                        DATE_TRUNC('week', evaluated_at)::DATE
                    ) STORED
);

CREATE INDEX IF NOT EXISTS idx_wheel_week_area
    ON lina.wheel_of_life (week_start, area);

COMMENT ON TABLE lina.wheel_of_life IS
    'Evaluaciones semanales de la Rueda de la Vida (8 áreas × 1-10).';
