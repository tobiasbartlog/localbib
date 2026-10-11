// Shared data for the knowledge-network smokes (#241): one built network, the
// way POST /api/analysis/build answers, small enough to lay out at once and
// shaped so every node kind occurs - two own Items, one shared reference
// (cited by both), two cited works at depth 1 and one deeper level.
export function network(over = {}) {
    return {
        nodes: [
            { id: 'p1', type: 'own', paper_id: 1, title: 'Earth construction basics', year: 2020, cited_by_count: 10, categories: [] },
            { id: 'p2', type: 'own', paper_id: 2, title: 'Printing with clay', year: 2021, cited_by_count: 5, categories: [] },
            { id: 'w1', type: 'missing', title: 'Building with earth', year: 2006, cited_by_count: 412, doi: '10.1000/earth' },
            { id: 'w2', type: 'external', title: 'Rammed earth walls', year: 2015, cited_by_count: 30 },
            { id: 'w3', type: 'external', title: 'Clay plasters', year: 2016, cited_by_count: 3 },
            { id: 'd1', type: 'depth2', title: 'Soil mechanics', year: 2001, cited_by_count: 50 },
        ],
        edges: [
            { source: 'p1', target: 'w1', edge_type: 'cites' },
            { source: 'p2', target: 'w1', edge_type: 'cites' },
            { source: 'p1', target: 'w2', edge_type: 'cites' },
            { source: 'p2', target: 'w3', edge_type: 'pdf_ref' },
            { source: 'w2', target: 'd1', edge_type: 'cites' },
        ],
        missing_sources: [
            { id: 'w1', title: 'Building with earth', authors: 'Minke, G.', year: 2006, referenced_by_count: 2, cited_by_count: 412 },
        ],
        own_paper_stats: [
            { paper_id: 1, title: 'Earth construction basics', authors: 'Doe, J.', year: 2020, cited_by_count: 10, reference_count: 2, pdf_reference_count: 0, in_openalex: true, doi: '10.1000/one' },
            { paper_id: 2, title: 'Printing with clay', authors: 'Roe, R.', year: 2021, cited_by_count: 5, reference_count: 1, pdf_reference_count: 1, in_openalex: true, doi: '10.1000/two' },
        ],
        // At the server's cap of 500, so the tab says "500+".
        referenced_papers: Array.from({ length: 500 }, (_, i) => ({
            openalex_id: 'W' + i, title: 'Cited work ' + i, authors: 'Author ' + i, year: 2000 + (i % 20),
            cited_by_count: i, referenced_by_own: 1, in_library: false, source: i % 2 ? 'pdf' : 'openalex',
        })),
        stats: {
            own_papers: 34,
            papers_found_in_openalex: 21,
            total_references: 3749,
            pdf_reference_edges: 2969,
            shared_references: 13,
            missing_sources: 13,
            depth: 3,
        },
        ...over,
    };
}

// Text of the analysis view only - the sidebar and other pages stay out of
// the checks on wording and on missing catalog keys.
export function analysisText() {
    const view = document.querySelector('.lb-analyse');
    return view ? view.textContent : '';
}

export function nodesOfKind(kind) {
    return [...document.querySelectorAll(`.lb-analyse circle[data-node-kind="${kind}"]`)];
}

// --- The drawn net (#242) ---------------------------------------------------
// A node's dot by id; SVG elements have no .click(), so a click is dispatched
// (it bubbles to the node's group, where d3 listens).
export function netNode(id) {
    return document.querySelector(`.lb-analyse circle[data-node-id="${id}"]`);
}

export function clickNode(id) {
    netNode(id).dispatchEvent(new MouseEvent('click', { bubbles: true }));
}

export function dimmedIds() {
    return [...document.querySelectorAll('.lb-analyse circle[data-node-id]')]
        .filter((c) => c.getAttribute('data-dimmed') === 'true')
        .map((c) => c.getAttribute('data-node-id'))
        .sort();
}

// The camera's zoom factor, read from the transform d3 writes.
export function camScale() {
    const tr = document.querySelector('.lb-analyse .lb-an-cam').getAttribute('transform') || '';
    const m = /scale\(([-\d.e]+)\)/.exec(tr);
    return m ? Number(m[1]) : 1;
}

export function pressKey(key, target = document) {
    target.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true }));
}

// The card of a selected work, once open.
export function card() {
    return document.querySelector('[data-testid="analysis-card"]');
}

// GET /api/analysis/node-abstract: an abstract for w1, none for the rest.
export const ABSTRACT_ROUTE = {
    method: 'GET',
    url: /^\/api\/analysis\/node-abstract\?/,
    reply: ({ url }) => ({ abstract: url.includes('openalex_id=w1') ? 'How earth is built.' : '' }),
};
