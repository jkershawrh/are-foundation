package repo

import (
	"context"
	"errors"
)

// AgentRec is persisted agent identity (metadata is JSON object).
type AgentRec struct {
	AgentType string
	OwnerID   string
	Metadata  map[string]any
	Status    string
	// AdmissionEnvelope optional immutable contract (demo BFF / REST parity with registry).
	AdmissionEnvelope map[string]any
}

var (
	ErrAgentNotFound    = errors.New("agent not found")
	ErrPassportConflict = errors.New("agent already has active passport")
)

// Repository backs RegisterAgent / GetAgent / IssuePassport idempotency.
type Repository interface {
	GetRegisterIdem(ctx context.Context, idemKey string) ([]byte, bool, error)
	// FinishRegister stores agent + idempotent response; returns body to return (cached on races).
	FinishRegister(ctx context.Context, idemKey, agentID string, responseBody []byte, rec AgentRec) ([]byte, error)

	GetAgent(ctx context.Context, agentID string) (AgentRec, bool, error)

	// GetAdmissionEnvelope returns stored envelope when present.
	GetAdmissionEnvelope(ctx context.Context, agentID string) (map[string]any, bool, error)

	GetPassportIdem(ctx context.Context, idemKey string) ([]byte, bool, error)
	// FinishPassport stores passport idempotency; ErrAgentNotFound if agent_id is unknown.
	// Returns ErrPassportConflict if agent already has an active passport and force is false.
	FinishPassport(ctx context.Context, idemKey, agentID string, responseBody []byte) ([]byte, error)
	ListPassportBodiesByAgent(ctx context.Context, agentID string) ([][]byte, error)
	GetPassportBody(ctx context.Context, passportID string) ([]byte, bool, error)

	// CheckAndRevokeActive checks if the agent has an active passport.
	// If force is true, revokes the active passport and returns (false, nil).
	// If force is false and an active passport exists, returns (true, nil).
	// Returns (hasConflict, error).
	CheckAndRevokeActive(ctx context.Context, agentID string, force bool, reason string) (bool, error)
	// RevokePassport marks a specific passport as REVOKED.
	RevokePassport(ctx context.Context, passportID, reason string) error
}
