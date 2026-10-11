// A recompute stream the test drives frame by frame (#213): the run stays in
// "running" until the test closes it, and an aborted request (Cancel) rejects
// the pending read the way a real fetch does. `install(calls)` wraps the
// route-table fetch mock and answers POST /api/maintenance/recompute with a new
// stream each time; `current()` is the last one.
import { vi } from 'vitest';

function controlledStream() {
    const encoder = new TextEncoder();
    const queue = [];
    let waiting = null;
    let closed = false;
    let failure = null;
    const settle = (fn) => {
        const w = waiting;
        waiting = null;
        if (w) fn(w);
    };
    const reader = {
        read: () => new Promise((resolve, reject) => {
            if (failure) return reject(failure);
            if (queue.length) return resolve({ done: false, value: queue.shift() });
            if (closed) return resolve({ done: true, value: undefined });
            waiting = { resolve, reject };
            return undefined;
        }),
        cancel: async () => {},
    };
    return {
        response: { ok: true, status: 200, body: { getReader: () => reader }, json: async () => ({}) },
        push(frame) {
            const chunk = encoder.encode(`data: ${JSON.stringify(frame)}\n\n`);
            if (waiting) settle((w) => w.resolve({ done: false, value: chunk }));
            else queue.push(chunk);
        },
        close() {
            closed = true;
            settle((w) => w.resolve({ done: true, value: undefined }));
        },
        abort() {
            failure = new DOMException('The operation was aborted.', 'AbortError');
            settle((w) => w.reject(failure));
        },
    };
}

export function installRecomputeStream(calls) {
    const base = globalThis.fetch;
    let stream = null;
    globalThis.fetch = vi.fn(async (url, options = {}) => {
        if (url === '/api/maintenance/recompute') {
            calls.push({ method: 'POST', url, body: JSON.parse(options.body) });
            stream = controlledStream();
            if (options.signal) options.signal.addEventListener('abort', () => stream.abort());
            return stream.response;
        }
        return base(url, options);
    });
    return { current: () => stream };
}

export function progress(steps, step, current, total, stats = {}) {
    return {
        type: 'progress', message: 'maintenance.recompute.step.' + step, step,
        index: steps.indexOf(step) + 1, steps, current, total,
        percent: total ? Math.floor((current * 100) / total) : 100, stats,
    };
}

export function done(steps, stats, errors = 0) {
    return { type: 'done', steps, stats, errors, total: 412 };
}
