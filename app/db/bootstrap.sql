-- Create login role `agent` with password (or reset if exists)
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'agent') THEN
    CREATE ROLE agent LOGIN PASSWORD 'agentpass';
  ELSE
    ALTER ROLE agent LOGIN PASSWORD 'agentpass';
  END IF;
END $$;

-- Create database `agentdb` owned by `agent`
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_database WHERE datname = 'agentdb') THEN
    CREATE DATABASE agentdb OWNER agent;
  END IF;
END $$;
