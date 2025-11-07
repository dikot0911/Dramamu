-- ==========================================================
-- MIGRATION: Webhook Idempotency & Payment Tracking
-- ==========================================================

-- Table untuk tracking processed webhooks (prevent duplicate actions)
CREATE TABLE IF NOT EXISTS webhook_events (
    id SERIAL PRIMARY KEY,
    event_id VARCHAR(255) UNIQUE NOT NULL,
    telegram_id BIGINT NOT NULL,
    action VARCHAR(50) NOT NULL,
    processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    data JSONB
);

CREATE INDEX IF NOT EXISTS idx_webhook_events_event_id ON webhook_events(event_id);
CREATE INDEX IF NOT EXISTS idx_webhook_events_telegram_id ON webhook_events(telegram_id);

-- Table untuk tracking payment transactions
CREATE TABLE IF NOT EXISTS payment_transactions (
    id SERIAL PRIMARY KEY,
    order_id VARCHAR(255) UNIQUE NOT NULL,
    telegram_id BIGINT NOT NULL,
    gross_amount INTEGER NOT NULL,
    payment_type VARCHAR(50),
    transaction_status VARCHAR(50) NOT NULL,
    transaction_id VARCHAR(255),
    fraud_status VARCHAR(50),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    midtrans_data JSONB
);

CREATE INDEX IF NOT EXISTS idx_payment_transactions_order_id ON payment_transactions(order_id);
CREATE INDEX IF NOT EXISTS idx_payment_transactions_telegram_id ON payment_transactions(telegram_id);
CREATE INDEX IF NOT EXISTS idx_payment_transactions_status ON payment_transactions(transaction_status);

-- Table untuk VIP history (optional - tracking VIP activations)
CREATE TABLE IF NOT EXISTS vip_history (
    id SERIAL PRIMARY KEY,
    telegram_id BIGINT NOT NULL,
    activated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    order_id VARCHAR(255),
    paket_id INTEGER,
    amount INTEGER
);

CREATE INDEX IF NOT EXISTS idx_vip_history_telegram_id ON vip_history(telegram_id);

-- Add updated_at trigger for payment_transactions
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ language 'plpgsql';

DROP TRIGGER IF EXISTS update_payment_transactions_updated_at ON payment_transactions;
CREATE TRIGGER update_payment_transactions_updated_at
    BEFORE UPDATE ON payment_transactions
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();
