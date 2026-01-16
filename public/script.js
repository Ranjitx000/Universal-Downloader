document.addEventListener('DOMContentLoaded', () => {
    const urlInput = document.getElementById('urlInput');
    const downloadBtn = document.getElementById('downloadBtn');
    const platformIcon = document.getElementById('platform-icon');
    
    // Auto-detect platform logic
    urlInput.addEventListener('input', (e) => {
        const val = e.target.value;
        const iconImg = document.getElementById('platform-icon-img');
        const iconEmoji = document.getElementById('platform-icon-emoji');

        function setIcon(type, src) {
            if (type === 'img') {
                iconImg.src = src;
                iconImg.classList.remove('hidden');
                iconEmoji.classList.add('hidden');
            } else {
                iconEmoji.textContent = src;
                iconEmoji.classList.remove('hidden');
                iconImg.classList.add('hidden');
            }
        }

        if (val.includes('youtube.com') || val.includes('youtu.be')) {
            setIcon('img', 'icons/youtube.png');
        } else if (val.includes('instagram.com')) {
            setIcon('img', 'icons/instagram.png');
        } else if (val.includes('facebook.com') || val.includes('fb.watch') || val.includes('web.facebook.com')) {
            setIcon('img', 'icons/facebook.png');
        } else if (val.includes('spotify.com')) {
             setIcon('img', 'icons/spotify.png');
            // Auto-select Audio for Spotify
            document.getElementById('formatSelect').value = 'audio';
        } else {
            setIcon('emoji', '🔗');
        }
    });

    downloadBtn.addEventListener('click', startDownloadProcess);
    
    // Enter key support
    urlInput.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') startDownloadProcess();
    });

    // History Logic
    loadHistory();
    document.getElementById('clearHistoryBtn').addEventListener('click', clearHistory);
});

async function startDownloadProcess() {
    const url = document.getElementById('urlInput').value.trim();
    const format = document.getElementById('formatSelect').value;
    const quality = document.getElementById('qualitySelect').value;
    
    if (!url) {
        showError('Please enter a valid URL');
        return;
    }

    resetUI();
    showStatus('Starting job...', 5);

    try {
        const response = await fetch('/api/download_job', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url: url, quality: quality, mode: format })
        });

        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'Failed to start job');

        pollJob(data.job_id);

    } catch (err) {
        showError(err.message);
        hideStatus();
    }
}

async function pollJob(jobId) {
    const pollInterval = setInterval(async () => {
        try {
            const res = await fetch(`/api/status/${jobId}`);
            const job = await res.json();

            if (job.status === 'downloading') {
                showStatus('Downloading media...', 45);
            } else if (job.status === 'fixing') {
                showStatus('Fixing Container...', 70);
            } else if (job.status === 'analyzing') {
                showStatus('Verifying Health...', 85);
            } else if (job.status === 'completed') {
                clearInterval(pollInterval);
                showStatus('Complete!', 100);
                setTimeout(() => {
                    hideStatus();
                    showResult(job, jobId);
                    saveToHistory(job, jobId);
                }, 800);
            } else if (job.status === 'error') {
                clearInterval(pollInterval);
                showError(job.error || 'Job failed');
                hideStatus();
            }
        } catch (err) {
            clearInterval(pollInterval);
            showError('Network error. Retrying...');
        }
    }, 1500);
}

function showResult(job, jobId) {
    const resultCard = document.getElementById('result-card');
    const title = document.getElementById('video-title');
    const uploader = document.getElementById('uploader');
    const healthContainer = document.getElementById('health-container');
    const healthVideo = document.getElementById('health-video');
    const healthAudio = document.getElementById('health-audio');
    const downloadLink = document.getElementById('download-link');
    const thumbnail = document.getElementById('thumbnail');
    const fileSize = document.getElementById('file-size');

    title.textContent = job.title || 'Downloaded Media';
    uploader.textContent = new Date().toLocaleDateString();
    fileSize.textContent = `Quality: ${job.quality || 'Standard'}`;
    
    // Health Data
    if (job.health) {
        healthContainer.textContent = (job.health.container || 'MP4').toUpperCase();
        healthVideo.textContent = job.health.video_codec || 'N/A';
        healthAudio.textContent = job.health.audio_codec || 'N/A';
    }

    // Set Download Link
    downloadLink.href = `/api/file/${jobId}`;
    
    thumbnail.style.display = 'none'; // Placeholder

    resultCard.classList.remove('hidden');
}

// History Functions
function saveToHistory(job, jobId) {
    const item = {
        id: jobId,
        title: job.title || 'Media',
        mode: job.mode || 'video',
        date: new Date().toLocaleDateString(),
        // We use the file link which is transient in backend (auto-delete), 
        // but for local history we might want to re-download. 
        // Note: Our backend deletes file AFTER serving. So history links 
        // will expire once clicked.
        // Ideally we should cache info, but for this "Download History" 
        // it serves as a log.
        // We can't re-download without re-starting job currently.
        // So let's treat this as a "Session Log".
    };
    
    // Actually, if we want persistent links, we'd need to NOT auto-delete or re-trigger.
    // For now, let's just log it. 
    
    const history = JSON.parse(localStorage.getItem('downloadHistory') || '[]');
    history.unshift(item);
    if (history.length > 10) history.pop(); // Keep last 10
    localStorage.setItem('downloadHistory', JSON.stringify(history));
    renderHistory();
}

function loadHistory() {
    renderHistory();
}

function renderHistory() {
    const list = document.getElementById('history-list');
    const container = document.getElementById('history-section');
    const history = JSON.parse(localStorage.getItem('downloadHistory') || '[]');

    if (history.length === 0) {
        container.classList.add('hidden');
        return;
    }

    container.classList.remove('hidden');
    list.innerHTML = history.map(item => `
        <li class="history-item">
            <span class="history-icon">${item.mode === 'audio' ? '🎵' : '🎬'}</span>
            <div class="history-details">
                <div class="history-title">${item.title}</div>
                <div class="history-meta">${item.date} • ${item.id.slice(0,8)}...</div>
            </div>
            <!-- Backend deletes file after download, so the link is one-time use usually.
                 If we want to allow re-download, we'd need to re-job it. 
                 For now, let's just show it as a record. -->
            <span class="history-meta" style="font-size: 0.7rem; opacity: 0.5;">Completed</span>
        </li>
    `).join('');
}

function clearHistory() {
    localStorage.removeItem('downloadHistory');
    renderHistory();
}

function resetUI() {
    document.getElementById('error-msg').classList.add('hidden');
    document.getElementById('result-card').classList.add('hidden');
}

function showStatus(text, percent) {
    const container = document.getElementById('status-container');
    const statusText = document.getElementById('status-text');
    const progressFill = document.getElementById('progress-fill');
    const progressPercent = document.getElementById('progress-percent');

    container.classList.remove('hidden');
    statusText.textContent = text;
    progressFill.style.width = `${percent}%`;
    progressPercent.textContent = `${percent}%`;
}

function hideStatus() {
    document.getElementById('status-container').classList.add('hidden');
}

function showError(msg) {
    const el = document.getElementById('error-msg');
    el.textContent = `⚠️ ${msg}`;
    el.classList.remove('hidden');
}
