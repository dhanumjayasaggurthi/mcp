import { useCallback, useEffect, useRef, useState } from 'react';
import { requestJson } from '../apiClient.js';

export function useResource(path) {
  const [state, setState] = useState({ data: null, loading: true, error: null, updatedAt: null });
  const [revision, setRevision] = useState(0);
  const refresh = useCallback(() => setRevision(value => value + 1), []);
  useEffect(() => {
    const controller = new AbortController();
    setState({ data: null, loading: Boolean(path), error: null, updatedAt: null });
    if (!path) return () => controller.abort();
    requestJson(path, { signal: controller.signal })
      .then(data => { if (!controller.signal.aborted) setState({ data, loading: false, error: null, updatedAt: new Date() }); })
      .catch(error => { if (!controller.signal.aborted) setState({ data: null, loading: false, error, updatedAt: null }); });
    return () => controller.abort();
  }, [path, revision]);
  return { ...state, refresh };
}

export function useCollection(kind) {
  const [state, setState] = useState({ items: [], next: null, loading: true, error: null });
  const active = useRef(null);
  const generation = useRef(0);
  const load = useCallback(async (after = '') => {
    active.current?.abort();
    const controller = new AbortController(); active.current = controller;
    const current = ++generation.current;
    setState(s => ({ ...s, ...(after ? {} : { items: [], next: null }), loading: true, error: null }));
    try {
      const body = await requestJson('/v1/control/' + kind + '?limit=100' + (after ? '&after=' + encodeURIComponent(after) : ''), { signal: controller.signal });
      if (!Array.isArray(body?.[kind])) throw new Error('The registry returned an invalid collection.');
      if (current === generation.current && !controller.signal.aborted) setState(s => ({
        items: after ? [...s.items, ...body[kind]] : body[kind], next: body.next_cursor,
        loading: false, error: null,
      }));
    } catch (error) {
      if (current === generation.current && !controller.signal.aborted) setState(s => ({ ...s, loading: false, error }));
    }
  }, [kind]);
  useEffect(() => { load(); return () => { ++generation.current; active.current?.abort(); }; }, [load]);
  return { ...state, refresh: () => load(), loadMore: () => load(state.next) };
}
