// Shared data for the Startup check smokes (#209): a report as
// GET /api/llm/startup-check sends it, and the LLM document it was made from.
export const CHECKED_AT = '2026-10-08T07:12:00+00:00';

const role = (over) => ({
    status: 'ok', detail_code: 'offered', connection_id: 'oai', connection_label: 'OpenAI',
    model: '', fallback: false, host: 'api.openai.com', http_status: 200, tasks: [], ...over,
});

export const REASONING_TASKS = ['research_chat', 'thesis_analysis', 'model_suggest', 'plugin'];
export const FAST_TASKS = ['categorize', 'metadata_extract', 'reference_extract', 'abstract', 'category_suggest', 'plugin_fast'];

// Reasoning's model is gone, the local embedding server does not answer,
// the simple tasks work.
export const BROKEN_REPORT = {
    checked_at: CHECKED_AT,
    running: false,
    roles: {
        reasoning: role({ status: 'model_missing', detail_code: 'not_offered', model: 'gpt-4o', tasks: REASONING_TASKS }),
        fast: role({ model: 'gpt-4o-mini', tasks: FAST_TASKS }),
        embedding: role({
            status: 'unreachable', detail_code: 'connection', connection_id: 'local',
            connection_label: 'Ollama (local)', model: 'nomic-embed-text', host: 'localhost:11434',
            http_status: null, tasks: ['semantic_search'],
        }),
    },
};

export const OK_REPORT = {
    checked_at: '2026-10-08T07:20:00+00:00',
    running: false,
    roles: {
        reasoning: role({ model: 'gpt-4o', tasks: REASONING_TASKS }),
        fast: role({ model: 'gpt-4o-mini', tasks: FAST_TASKS }),
        // A provider without a model list is no alarm.
        embedding: role({
            status: 'unchecked', detail_code: 'no_model_list', connection_id: 'local',
            connection_label: 'Ollama (local)', model: 'nomic-embed-text', host: 'localhost:11434',
            http_status: 404, tasks: ['semantic_search'],
        }),
    },
};

const unbound = (tasks) => role({
    status: 'unbound', detail_code: 'not_bound', connection_id: '', connection_label: '',
    host: '', http_status: null, tasks,
});

export const UNBOUND_REPORT = {
    checked_at: CHECKED_AT,
    running: false,
    roles: { reasoning: unbound(REASONING_TASKS), fast: unbound(FAST_TASKS), embedding: unbound(['semantic_search']) },
};

export const CONFIG = {
    connections: [
        { id: 'oai', label: 'OpenAI', provider: 'openai', base_url: '', has_key: true, key_hint: 'sk-…4f2a' },
        { id: 'local', label: 'Ollama (local)', provider: 'custom', base_url: 'http://localhost:11434/v1', has_key: false, key_hint: '' },
    ],
    roles: {
        reasoning: { connection_id: 'oai', model: 'gpt-4o' },
        fast: { connection_id: 'oai', model: 'gpt-4o-mini' },
        embedding: { connection_id: 'local', model: 'nomic-embed-text' },
    },
    status: { reasoning: true, fast: true, embedding: true, connections: 2 },
};

// An installation past the onboarding with its roles bound: the routes the
// boot asks, with `report` as the Startup check's answer.
export function setUpRoutes(report) {
    return [
        { method: 'GET', url: '/api/settings', reply: { ONBOARDING_COMPLETED: 'true' } },
        { method: 'GET', url: '/api/llm/status', reply: CONFIG.status },
        { method: 'GET', url: '/api/llm/config', reply: CONFIG },
        { method: 'GET', url: '/api/llm/startup-check', reply: report },
    ];
}
