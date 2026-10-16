# Maps EvaluatePolicy JSON input to { effect, reason }.
# Query: data.are.evaluatepolicy.decision (POST /v1/data/are/evaluatepolicy/decision)
#
# This policy is intentionally small enough to inspect while still modeling a
# real foundation rule: model promotion is allowed only for governed model
# resources and remains check-only at the API boundary.
package are.evaluatepolicy

default decision = {"effect": "DENY", "reason": "no policy matched action class"}

decision = {"effect": "DENY", "reason": "forbidden action class for demo"} {
	input.action_class == "demo.forbidden_action"
} else = {"effect": "ALLOW", "reason": "read scope allowed"} {
	input.action_class == "demo.read"
	startswith(input.resource, "urn:dataset/")
} else = {"effect": "ALLOW", "reason": "model promotion policy passed for governed model resource"} {
	input.action_class == "model.promote_to_production"
	input.agent_id != ""
	startswith(input.resource, "model/")
	not contains(lower(input.resource), "experimental")
} else = {"effect": "DENY", "reason": "model promotion requires a governed non-experimental model resource"} {
	input.action_class == "model.promote_to_production"
} else = {"effect": "ALLOW", "reason": "parts lookup is read-only and always permitted"} {
	input.action_class == "pdm.parts.lookup"
	input.agent_id != ""
} else = {"effect": "ALLOW", "reason": "BOM query is read-only and always permitted"} {
	input.action_class == "pdm.parts.bom_query"
	input.agent_id != ""
} else = {"effect": "ALLOW", "reason": "document access permitted for non-classified documents"} {
	input.action_class == "pdm.document.access"
	input.agent_id != ""
	not contains(lower(input.resource), "classified")
} else = {"effect": "DENY", "reason": "classified document access requires elevated clearance not held by this agent"} {
	input.action_class == "pdm.document.access_classified"
} else = {"effect": "ALLOW", "reason": "model promotion approved for governed production-ready model"} {
	input.action_class == "pdm.model.promote"
	input.agent_id != ""
	startswith(input.resource, "model/")
	not contains(lower(input.resource), "experimental")
} else = {"effect": "DENY", "reason": "experimental model promotion requires human review and approval"} {
	input.action_class == "pdm.model.promote"
	contains(lower(input.resource), "experimental")
} else = {"effect": "DENY", "reason": "external CAD sharing denied - requires explicit partner-sharing scope"} {
	input.action_class == "pdm.cad.share_external"
} else = {"effect": "DENY", "reason": "action class contains deny"} {
	contains(upper(input.action_class), "DENY")
} else = {"effect": "ALLOW", "reason": "policy-evaluated"}
