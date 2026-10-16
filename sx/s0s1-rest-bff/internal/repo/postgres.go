package repo

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jackc/pgx/v5/pgxpool"
)

// Postgres persists agents and idempotent REST payloads.
type Postgres struct {
	pool *pgxpool.Pool
}

func NewPostgres(ctx context.Context, connString string) (*Postgres, error) {
	pool, err := pgxpool.New(ctx, connString)
	if err != nil {
		return nil, err
	}
	p := &Postgres{pool: pool}
	if err := p.migrate(ctx); err != nil {
		pool.Close()
		return nil, err
	}
	return p, nil
}

func (p *Postgres) Close() {
	p.pool.Close()
}

func (p *Postgres) migrate(ctx context.Context) error {
	stmts := []string{
		`CREATE TABLE IF NOT EXISTS s0s1_agents (
	agent_id TEXT PRIMARY KEY,
	agent_type TEXT NOT NULL,
	owner_id TEXT NOT NULL,
	metadata_json JSONB NOT NULL DEFAULT '{}',
	status TEXT NOT NULL
)`,
		`CREATE TABLE IF NOT EXISTS s0s1_register_idem (
	idem_key TEXT PRIMARY KEY,
	response_body BYTEA NOT NULL
)`,
		`CREATE TABLE IF NOT EXISTS s0s1_passport_idem (
	idem_key TEXT PRIMARY KEY,
	response_body BYTEA NOT NULL
)`,
		`CREATE TABLE IF NOT EXISTS s0s1_passports (
	passport_id TEXT PRIMARY KEY,
	agent_id TEXT NOT NULL,
	status TEXT NOT NULL DEFAULT 'ACTIVE',
	response_body BYTEA NOT NULL
)`,
		`DO $$ BEGIN
	IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_s0s1_passports_agent_active') THEN
		CREATE UNIQUE INDEX idx_s0s1_passports_agent_active ON s0s1_passports(agent_id) WHERE status = 'ACTIVE';
	END IF;
END $$`,
		`ALTER TABLE s0s1_agents ADD COLUMN IF NOT EXISTS admission_envelope_json JSONB`,
	}
	for _, s := range stmts {
		if _, err := p.pool.Exec(ctx, s); err != nil {
			return err
		}
	}
	return nil
}

func isUniqueViolation(err error) bool {
	var pgErr *pgconn.PgError
	return errors.As(err, &pgErr) && pgErr.Code == "23505"
}

func (p *Postgres) GetRegisterIdem(ctx context.Context, idemKey string) ([]byte, bool, error) {
	var body []byte
	err := p.pool.QueryRow(ctx, `SELECT response_body FROM s0s1_register_idem WHERE idem_key = $1`, idemKey).Scan(&body)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, false, nil
	}
	if err != nil {
		return nil, false, err
	}
	return body, true, nil
}

func (p *Postgres) FinishRegister(ctx context.Context, idemKey, agentID string, responseBody []byte, rec AgentRec) ([]byte, error) {
	meta := map[string]any{}
	if rec.Metadata != nil {
		meta = rec.Metadata
	}
	metaJSON, err := json.Marshal(meta)
	if err != nil {
		return nil, err
	}
	envJSON := []byte("null")
	if rec.AdmissionEnvelope != nil {
		envJSON, err = json.Marshal(rec.AdmissionEnvelope)
		if err != nil {
			return nil, err
		}
	}
	for range 8 {
		tx, err := p.pool.Begin(ctx)
		if err != nil {
			return nil, err
		}
		var cached []byte
		err = tx.QueryRow(ctx, `SELECT response_body FROM s0s1_register_idem WHERE idem_key = $1`, idemKey).Scan(&cached)
		if err == nil {
			_ = tx.Rollback(ctx)
			return cached, nil
		}
		if !errors.Is(err, pgx.ErrNoRows) {
			_ = tx.Rollback(ctx)
			return nil, err
		}
		_, err = tx.Exec(ctx, `
INSERT INTO s0s1_agents (agent_id, agent_type, owner_id, metadata_json, status, admission_envelope_json)
VALUES ($1, $2, $3, $4::jsonb, $5, $6::jsonb)
ON CONFLICT (agent_id) DO NOTHING`,
			agentID, rec.AgentType, rec.OwnerID, metaJSON, rec.Status, envJSON)
		if err != nil {
			_ = tx.Rollback(ctx)
			return nil, err
		}
		_, err = tx.Exec(ctx, `INSERT INTO s0s1_register_idem (idem_key, response_body) VALUES ($1, $2)`, idemKey, responseBody)
		if err == nil {
			if err := tx.Commit(ctx); err != nil {
				return nil, err
			}
			return responseBody, nil
		}
		_ = tx.Rollback(ctx)
		if isUniqueViolation(err) {
			continue
		}
		return nil, err
	}
	return nil, fmt.Errorf("register idem race: %s", idemKey)
}

func (p *Postgres) GetAgent(ctx context.Context, agentID string) (AgentRec, bool, error) {
	var rec AgentRec
	var metaJSON []byte
	err := p.pool.QueryRow(ctx, `
SELECT agent_type, owner_id, metadata_json, status FROM s0s1_agents WHERE agent_id = $1`, agentID).Scan(
		&rec.AgentType, &rec.OwnerID, &metaJSON, &rec.Status)
	if errors.Is(err, pgx.ErrNoRows) {
		return AgentRec{}, false, nil
	}
	if err != nil {
		return AgentRec{}, false, err
	}
	if len(metaJSON) > 0 {
		if err := json.Unmarshal(metaJSON, &rec.Metadata); err != nil {
			log.Printf(
				"s0s1-rest-bff FM-001: agent metadata_json unmarshal failed agent_id=%s err=%v",
				agentID, err,
			)
			rec.Metadata = nil
		}
	}
	if rec.Metadata == nil {
		rec.Metadata = map[string]any{}
	}
	return rec, true, nil
}

func (p *Postgres) GetPassportIdem(ctx context.Context, idemKey string) ([]byte, bool, error) {
	var body []byte
	err := p.pool.QueryRow(ctx, `SELECT response_body FROM s0s1_passport_idem WHERE idem_key = $1`, idemKey).Scan(&body)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, false, nil
	}
	if err != nil {
		return nil, false, err
	}
	return body, true, nil
}

func (p *Postgres) FinishPassport(ctx context.Context, idemKey, agentID string, responseBody []byte) ([]byte, error) {
	for range 8 {
		tx, err := p.pool.Begin(ctx)
		if err != nil {
			return nil, err
		}
		var cached []byte
		err = tx.QueryRow(ctx, `SELECT response_body FROM s0s1_passport_idem WHERE idem_key = $1`, idemKey).Scan(&cached)
		if err == nil {
			_ = tx.Rollback(ctx)
			return cached, nil
		}
		if !errors.Is(err, pgx.ErrNoRows) {
			_ = tx.Rollback(ctx)
			return nil, err
		}
		var one int
		err = tx.QueryRow(ctx, `SELECT 1 FROM s0s1_agents WHERE agent_id = $1`, agentID).Scan(&one)
		if errors.Is(err, pgx.ErrNoRows) {
			_ = tx.Rollback(ctx)
			return nil, ErrAgentNotFound
		}
		if err != nil {
			_ = tx.Rollback(ctx)
			return nil, err
		}
		_, err = tx.Exec(ctx, `INSERT INTO s0s1_passport_idem (idem_key, response_body) VALUES ($1, $2)`, idemKey, responseBody)
		if err == nil {
			if passportID := passportIDFromBody(responseBody); passportID != "" {
				_, err = tx.Exec(ctx, `
INSERT INTO s0s1_passports (passport_id, agent_id, response_body)
VALUES ($1, $2, $3)
ON CONFLICT (passport_id) DO UPDATE SET response_body = EXCLUDED.response_body`,
					passportID, agentID, responseBody)
			}
		}
		if err == nil {
			if err := tx.Commit(ctx); err != nil {
				return nil, err
			}
			return responseBody, nil
		}
		_ = tx.Rollback(ctx)
		if isUniqueViolation(err) {
			continue
		}
		return nil, err
	}
	return nil, fmt.Errorf("passport idem race: %s", idemKey)
}

func (p *Postgres) ListPassportBodiesByAgent(ctx context.Context, agentID string) ([][]byte, error) {
	rows, err := p.pool.Query(ctx, `SELECT response_body, status FROM s0s1_passports WHERE agent_id = $1 ORDER BY passport_id`, agentID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := [][]byte{}
	for rows.Next() {
		var body []byte
		var status string
		if err := rows.Scan(&body, &status); err != nil {
			return nil, err
		}
		var parsed map[string]any
		if err := json.Unmarshal(body, &parsed); err == nil {
			if pp, ok := parsed["passport"].(map[string]any); ok {
				pp["status"] = status
				if updated, err := json.Marshal(parsed); err == nil {
					body = updated
				}
			}
		}
		out = append(out, body)
	}
	return out, rows.Err()
}

func (p *Postgres) GetPassportBody(ctx context.Context, passportID string) ([]byte, bool, error) {
	var body []byte
	var status string
	err := p.pool.QueryRow(ctx, `SELECT response_body, status FROM s0s1_passports WHERE passport_id = $1`, passportID).Scan(&body, &status)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, false, nil
	}
	if err != nil {
		return nil, false, err
	}
	var parsed map[string]any
	if err := json.Unmarshal(body, &parsed); err == nil {
		if pp, ok := parsed["passport"].(map[string]any); ok {
			pp["status"] = status
			if updated, err := json.Marshal(parsed); err == nil {
				return updated, true, nil
			}
		}
	}
	return body, true, nil
}

func (p *Postgres) GetAdmissionEnvelope(ctx context.Context, agentID string) (map[string]any, bool, error) {
	var raw []byte
	err := p.pool.QueryRow(ctx, `SELECT admission_envelope_json FROM s0s1_agents WHERE agent_id = $1`, agentID).Scan(&raw)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, false, nil
	}
	if err != nil {
		return nil, false, err
	}
	if len(raw) == 0 || string(raw) == "null" {
		return nil, false, nil
	}
	var out map[string]any
	if err := json.Unmarshal(raw, &out); err != nil {
		return nil, false, err
	}
	if len(out) == 0 {
		return nil, false, nil
	}
	return out, true, nil
}

func (p *Postgres) CheckAndRevokeActive(ctx context.Context, agentID string, force bool, reason string) (bool, error) {
	tx, err := p.pool.Begin(ctx)
	if err != nil {
		return false, err
	}
	defer func() { _ = tx.Rollback(ctx) }()

	var one int
	err = tx.QueryRow(ctx, `SELECT 1 FROM s0s1_passports WHERE agent_id = $1 AND status = 'ACTIVE' LIMIT 1 FOR UPDATE`, agentID).Scan(&one)
	if errors.Is(err, pgx.ErrNoRows) {
		_ = tx.Commit(ctx)
		return false, nil
	}
	if err != nil {
		return false, err
	}
	if !force {
		_ = tx.Commit(ctx)
		return true, nil
	}
	_, err = tx.Exec(ctx, `UPDATE s0s1_passports SET status = 'REVOKED' WHERE agent_id = $1 AND status = 'ACTIVE'`, agentID)
	if err != nil {
		return false, err
	}
	return false, tx.Commit(ctx)
}

func (p *Postgres) RevokePassport(ctx context.Context, passportID, reason string) error {
	tag, err := p.pool.Exec(ctx, `UPDATE s0s1_passports SET status = 'REVOKED' WHERE passport_id = $1 AND status = 'ACTIVE'`, passportID)
	if err != nil {
		return err
	}
	if tag.RowsAffected() == 0 {
		return ErrAgentNotFound
	}
	return nil
}
