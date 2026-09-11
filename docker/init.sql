-- Initialisatiescript voor PostgreSQL + pgvector
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "vector";

-- Type enum voor draft statussen
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'draft_status_enum') THEN
        CREATE TYPE draft_status_enum AS ENUM ('draft', 'superseded', 'approved', 'rejected');
    END IF;
END$$;
