function workflowIsPlainObject(value) {
    return value && typeof value === "object" && !Array.isArray(value);
}

function workflowIsEmpty(value) {
    if (value === null || value === undefined || value === "") return true;
    if (Array.isArray(value)) return value.length === 0;
    if (workflowIsPlainObject(value)) return Object.keys(value).length === 0;
    return false;
}

function workflowFormatValue(value) {
    if (value === null || value === undefined) return "none";
    if (typeof value === "boolean") return value ? "true" : "false";
    if (Array.isArray(value) || workflowIsPlainObject(value)) {
        return JSON.stringify(value, null, 2);
    }
    return String(value);
}

function workflowField(label, value, extraClass = "") {
    return `
        <div class="ai-field">
            <div class="ai-field-label">${escapeHtml(label)}</div>
            <div class="ai-field-value ${extraClass}">${escapeHtml(workflowFormatValue(value))}</div>
        </div>
    `;
}

function workflowStructuredField(label, value) {
    if (workflowIsEmpty(value)) return "";
    return `
        <div class="ai-field">
            <div class="ai-field-label">${escapeHtml(label)}</div>
            <pre class="ai-structured-value">${escapeHtml(workflowFormatValue(value))}</pre>
        </div>
    `;
}

function workflowExecutionBlock(ruleResult) {
    if (!ruleResult || (!ruleResult.check_type && !ruleResult.result_description)) {
        return workflowField("Execution", "No check executed this turn");
    }

    const isSuccess = ruleResult.success !== false;
    const resultClass = isSuccess ? "success" : "failure";
    const resultText = ruleResult.critical_success ? "Critical success" :
        ruleResult.critical_failure ? "Critical failure" :
        isSuccess ? "Success" : "Failure";

    let html = workflowField("Execution Type", ruleResult.check_type || "none");
    if (ruleResult.skill) html += workflowField("Skill", ruleResult.skill);
    if (ruleResult.difficulty) html += workflowField("Difficulty", ruleResult.difficulty);
    if (ruleResult.roll !== undefined && ruleResult.roll !== null) {
        const threshold = ruleResult.threshold ?? ruleResult.player_skill ?? "?";
        html += workflowField("Roll", `${ruleResult.roll} / ${threshold}`);
    }
    html += workflowField("Result", ruleResult.result_description || resultText, resultClass);
    return html;
}

window.updateRulePanel = function updateRulePanel(rulePlan, ruleResult, hardChanges) {
    const panel = document.getElementById("rule-panel");
    if (!panel) return;
    if (!rulePlan && !ruleResult) {
        panel.innerHTML = `<p class="placeholder-text">Waiting for the next action...</p>`;
        return;
    }

    const normalizedAction = workflowIsPlainObject(rulePlan && rulePlan.normalized_action) ? rulePlan.normalized_action : {};
    const feasibility = workflowIsPlainObject(rulePlan && rulePlan.feasibility) ? rulePlan.feasibility : {};
    const checkPlan = workflowIsPlainObject(rulePlan && rulePlan.check) ? rulePlan.check : {};

    let html = "";
    html += workflowStructuredField("Action Plan", normalizedAction);
    html += workflowField(
        "Feasibility",
        feasibility.ok === false ? "blocked" : "allowed",
        feasibility.ok === false ? "failure" : "success"
    );
    if (feasibility.reason) html += workflowField("Block Reason", feasibility.reason);
    html += workflowStructuredField("Check Plan", checkPlan);
    html += workflowExecutionBlock(ruleResult);
    html += workflowStructuredField("Hard Changes", hardChanges);

    panel.innerHTML = html || `<p class="placeholder-text">Waiting for the next action...</p>`;
};

window.updateRhythmPanel = function updateRhythmPanel(result) {
    const panel = document.getElementById("rhythm-panel");
    if (!panel) return;
    if (!result) {
        panel.innerHTML = `<p class="placeholder-text">Waiting for the next action...</p>`;
        return;
    }

    const feasibleText = result.feasible !== false ? "allowed" : "blocked";
    const feasibleClass = result.feasible !== false ? "success" : "failure";

    let html = workflowField("Decision", feasibleText, feasibleClass);
    if (result.hint) html += workflowField("Hint", result.hint);
    if (result.stage_assessment) html += workflowField("Stage", result.stage_assessment);
    html += workflowStructuredField("NPC Guide", result.npc_action_guide);
    html += workflowStructuredField("Soft Changes", result.soft_world_changes);
    html += workflowStructuredField("Merged World Changes", result.world_changes);
    html += workflowStructuredField("Location Context", result.location_context);
    if (!workflowIsEmpty(result.object_context)) html += workflowStructuredField("Object Context", result.object_context);
    if (!workflowIsEmpty(result.npc_context)) html += workflowStructuredField("NPC Context", result.npc_context);

    panel.innerHTML = html || `<p class="placeholder-text">Waiting for the next action...</p>`;
};

window.checkExistingSession = async function checkExistingSession() {
    try {
        const resp = await fetch("/trpg/api/state");
        const data = await resp.json();
        if (!data.game_started) return;

        showGameUI();
        if (data.chat_messages) {
            data.chat_messages.forEach((msg) => {
                addMessage(msg.role, msg.content, false);
            });
            scrollToBottom();
        }
        if (data.game_state) {
            updatePlayerStatus(data.game_state);
        }
        if (data.last_workflow) {
            updateRulePanel(
                data.last_workflow.rule_plan,
                data.last_workflow.rule_result,
                data.last_workflow.hard_changes
            );
            updateRhythmPanel(data.last_workflow.rhythm_result);
        }
        if (data.map_data) {
            currentMapData = data.map_data;
            renderMap(data.map_data);
        }
    } catch (err) {
        // first visit, keep module selection visible
    }
};
