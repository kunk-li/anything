// ============================================================
// Anything Frontend — 极简 IndexedDB KV store (Task XXXX-9 #156)
//
// 为啥需要:
//   localStorage 5MB 上限. workflows / draft messages / 历史快照
//   一旦超过就 QuotaExceededError 写不进去, 静默丢失数据.
//   IndexedDB 通常 50%+ 磁盘可用, 量级 GB.
//
// API (Promise 风格, 但 _loadWorkflows 用 sync mirror):
//   const store = await IdbStore.open('anything', 'kv')
//   await store.set('workflows', [...])
//   const v = await store.get('workflows')
//   await store.del('key')
//   await store.keys()       // 列所有 key
//   await store.clear()
//
// 主程用 sync mirror 模式:
//   IdbStore.openMirror('anything', 'kv', ['workflows'])
//     -> 返 {get(k): sync, set(k, v): sync (fire-and-forget IDB write)}
//     -> 启动 await 一次, 把所选 keys 全拉到内存
//     -> 之后 sync 操作内存, 后台异步刷盘
//   适合"读频繁, 写少, 容忍异步刷盘"的场景 (workflows 完全符合)
// ============================================================

const IdbStore = (() => {
    function open(dbName, storeName) {
        return new Promise((resolve, reject) => {
            if (!window.indexedDB) {
                reject(new Error('IndexedDB unsupported'));
                return;
            }
            const req = indexedDB.open(dbName, 1);
            req.onupgradeneeded = (e) => {
                const db = e.target.result;
                if (!db.objectStoreNames.contains(storeName)) {
                    db.createObjectStore(storeName);
                }
            };
            req.onsuccess = (e) => {
                const db = e.target.result;
                resolve({
                    get(key) {
                        return new Promise((res, rej) => {
                            const tx = db.transaction(storeName, 'readonly');
                            const r = tx.objectStore(storeName).get(key);
                            r.onsuccess = () => res(r.result);
                            r.onerror = () => rej(r.error);
                        });
                    },
                    set(key, val) {
                        return new Promise((res, rej) => {
                            const tx = db.transaction(storeName, 'readwrite');
                            const r = tx.objectStore(storeName).put(val, key);
                            r.onsuccess = () => res();
                            r.onerror = () => rej(r.error);
                        });
                    },
                    del(key) {
                        return new Promise((res, rej) => {
                            const tx = db.transaction(storeName, 'readwrite');
                            const r = tx.objectStore(storeName).delete(key);
                            r.onsuccess = () => res();
                            r.onerror = () => rej(r.error);
                        });
                    },
                    keys() {
                        return new Promise((res, rej) => {
                            const tx = db.transaction(storeName, 'readonly');
                            const r = tx.objectStore(storeName).getAllKeys();
                            r.onsuccess = () => res(r.result);
                            r.onerror = () => rej(r.error);
                        });
                    },
                    clear() {
                        return new Promise((res, rej) => {
                            const tx = db.transaction(storeName, 'readwrite');
                            const r = tx.objectStore(storeName).clear();
                            r.onsuccess = () => res();
                            r.onerror = () => rej(r.error);
                        });
                    },
                });
            };
            req.onerror = () => reject(req.error);
        });
    }

    /**
     * 启动一次 await, 把指定 keys 全部加载到内存 mirror.
     * 之后 get(k) / set(k, v) 是同步操作 (操作 mirror), 后台异步写 IDB.
     *
     * 适合"读多写少, 不在乎写延迟" 的场景 (workflows / drafts).
     */
    async function openMirror(dbName, storeName, mirrorKeys) {
        const mirror = new Map();
        let store = null;
        try {
            store = await open(dbName, storeName);
            for (const k of mirrorKeys) {
                try {
                    const v = await store.get(k);
                    if (v !== undefined) mirror.set(k, v);
                } catch (_) {}
            }
        } catch (e) {
            // IDB 不可用 (隐私模式 / 老浏览器), 降级到纯内存 (会话内有效, 刷新丢)
            console.warn('[idb-store] IDB unavailable, falling back to in-memory:', e.message);
        }
        return {
            get(key) {
                return mirror.has(key) ? mirror.get(key) : undefined;
            },
            set(key, val) {
                mirror.set(key, val);
                // 异步刷盘, 不 await — 失败不影响主流程
                if (store) store.set(key, val).catch((e) => {
                    console.warn('[idb-store] write failed for', key, e.message);
                });
            },
            del(key) {
                mirror.delete(key);
                if (store) store.del(key).catch(() => {});
            },
            available: !!store,
        };
    }

    return { open, openMirror };
})();

if (typeof window !== 'undefined') window.IdbStore = IdbStore;
