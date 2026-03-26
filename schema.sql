-- Simple WA Checker Bot — Supabase Schema
-- Run this in Supabase SQL Editor

-- WA Accounts
CREATE TABLE IF NOT EXISTS wa_accounts (
    id              BIGSERIAL PRIMARY KEY,
    account_id      TEXT UNIQUE NOT NULL,
    label           TEXT NOT NULL,
    phone_number    TEXT,
    is_connected    INTEGER DEFAULT 0,
    total_checks    INTEGER DEFAULT 0,
    last_connected  TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Bot Settings (key-value store)
CREATE TABLE IF NOT EXISTS bot_settings (
    id         BIGSERIAL PRIMARY KEY,
    key        TEXT UNIQUE NOT NULL,
    value      TEXT,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- API Endpoints (keyless — endpoint_id IS the URL)
CREATE TABLE IF NOT EXISTS api_endpoints (
    id              BIGSERIAL PRIMARY KEY,
    endpoint_id     TEXT UNIQUE NOT NULL,
    label           TEXT NOT NULL,
    owner_id        BIGINT,
    total_requests  INTEGER DEFAULT 0,
    is_active       BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Baileys sessions (for auto-restore after redeploy)
CREATE TABLE IF NOT EXISTS whatsapp_sessions (
    id           BIGSERIAL PRIMARY KEY,
    session_id   TEXT UNIQUE NOT NULL,
    session_data JSONB,
    is_connected BOOLEAN DEFAULT FALSE,
    phone_number TEXT,
    updated_at   TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_wa_accounts_account_id ON wa_accounts(account_id);
CREATE INDEX IF NOT EXISTS idx_api_endpoints_endpoint_id ON api_endpoints(endpoint_id);
CREATE INDEX IF NOT EXISTS idx_bot_settings_key ON bot_settings(key);
