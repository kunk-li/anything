// ============================================================
// frontend/static/modules/upload-manager.js  (Task SS #79)
//
// 多文件上传 (Task P) — uploadFiles / _renderQueueRow / uploadFile /
// triggerBuildIndex. 从原 app.js 拆出, 走 deps 注入 els/t/toast.
// ============================================================

(function () {
    window.AnythingApp = window.AnythingApp || {};

    window.AnythingApp.uploadManager = function uploadManager(deps) {
        const { els, t, toast } = deps;

        /**
         * 多文件按顺序上传 (避免并发触发 embedding GPU/CPU 抢占).
         * 每个文件状态: pending -> uploading -> indexing -> done / error
         */
        async function uploadFiles(fileList) {
            const files = Array.from(fileList || []).filter(f => f && f.size != null);
            if (!files.length) return;
            els.uploadQueue.innerHTML = '';
            const rows = files.map((f, i) => _renderQueueRow(i, f, 'pending'));
            let okCount = 0;
            let failCount = 0;
            for (let i = 0; i < files.length; i++) {
                const file = files[i];
                _renderQueueRow(i, file, 'uploading', null, rows[i]);
                try {
                    const { payload, status } = await ApiClient.uploadDocument(file);
                    if (status === 200 && payload?.code === 'SUCCESS') {
                        const d = payload.data || {};
                        if (d.indexed && d.index_summary) {
                            const s = d.index_summary;
                            _renderQueueRow(i, file, 'done',
                                `${s.total_chunks} chunks · ${s.total_vectors} vectors`, rows[i]);
                        } else if (d.index_error) {
                            _renderQueueRow(i, file, 'error', `索引失败: ${d.index_error}`, rows[i]);
                            failCount++;
                            continue;
                        } else {
                            _renderQueueRow(i, file, 'done', '仅落盘, 未索引', rows[i]);
                        }
                        okCount++;
                    } else {
                        _renderQueueRow(i, file, 'error',
                            `${payload?.code || status} ${payload?.message || ''}`, rows[i]);
                        failCount++;
                    }
                } catch (e) {
                    _renderQueueRow(i, file, 'error', e.message, rows[i]);
                    failCount++;
                }
            }
            els.uploadResult.className = failCount === 0 ? 'upload-result success' : 'upload-result';
            els.uploadResult.textContent = `批量上传完成: ${okCount} 成功 / ${failCount} 失败 (共 ${files.length})`;
            if (failCount === 0) {
                toast('success', t('toast.upload.success'), `${okCount} files`);
            } else {
                toast('error', t('toast.upload.fail'), `${failCount} of ${files.length} failed`);
            }
        }

        function _renderQueueRow(index, file, state, detail, existingRow) {
            const ICONS = { pending: '⋯', uploading: '⬆', indexing: '⚙', done: '✓', error: '✗' };
            let row = existingRow;
            if (!row) {
                row = document.createElement('li');
                row.className = 'queue-item';
                row.innerHTML = `
                    <span class="queue-icon"></span>
                    <span class="queue-name"></span>
                    <span class="queue-size"></span>
                    <span class="queue-detail"></span>
                `;
                els.uploadQueue.appendChild(row);
            }
            row.dataset.state = state;
            row.querySelector('.queue-icon').textContent = ICONS[state] || '?';
            row.querySelector('.queue-name').textContent = file.name;
            row.querySelector('.queue-size').textContent =
                `${(file.size / 1024).toFixed(1)} KB`;
            row.querySelector('.queue-detail').textContent = detail || '';
            return row;
        }

        async function uploadFile(file) {
            return uploadFiles([file]);
        }

        async function triggerBuildIndex() {
            els.jobResult.className = 'job-result';
            els.jobResult.textContent = '提交构建任务...';
            try {
                const { payload, status } = await ApiClient.buildIndex();
                if (status === 200 && payload?.code === 'SUCCESS') {
                    const jobId = payload.data?.job_id;
                    els.jobResult.className = 'job-result success';
                    els.jobResult.textContent = `✓ ${jobId}`;
                    toast('success', t('toast.index.triggered'), `job_id=${jobId}`);
                } else {
                    els.jobResult.className = 'job-result error';
                    els.jobResult.textContent = `× ${payload?.code || status} ${payload?.message || ''}`;
                }
            } catch (e) {
                els.jobResult.className = 'job-result error';
                els.jobResult.textContent = `× ${e.message}`;
            }
        }

        return { uploadFiles, _renderQueueRow, uploadFile, triggerBuildIndex };
    };
})();
