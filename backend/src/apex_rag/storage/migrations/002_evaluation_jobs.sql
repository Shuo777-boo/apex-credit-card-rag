CREATE TABLE IF NOT EXISTS evaluation_jobs (
 job_id uuid PRIMARY KEY, idempotency_key uuid NOT NULL UNIQUE, request_hash text NOT NULL,
 data jsonb NOT NULL, cancel_requested boolean NOT NULL DEFAULT false,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);
