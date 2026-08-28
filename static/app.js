(() => {
    "use strict";

    const MAX_FILE_BYTES = 25 * 1024 * 1024;
    const ALLOWED_EXTENSIONS = new Set(["wav", "mp3", "m4a", "aac", "flac", "ogg", "opus", "webm"]);
    const TARGET_SAMPLE_RATE = 16000;
    const WS_BACKPRESSURE_LIMIT = 256 * 1024;

    const $ = (id) => document.getElementById(id);
    const elements = {
        serviceState: $("service-state"),
        serviceStateText: $("service-state-text"),
        dashboardButton: $("dashboard-button"),
        systemDashboard: $("system-dashboard"),
        dashboardApi: $("dashboard-api"),
        dashboardModel: $("dashboard-model"),
        dashboardModelName: $("dashboard-model-name"),
        dashboardDevice: $("dashboard-device"),
        dashboardTransport: $("dashboard-transport"),
        dashboardVersion: $("dashboard-version"),
        dashboardAudit: $("dashboard-audit"),
        consentCheckbox: $("consent-checkbox"),
        uploadForm: $("upload-form"),
        audioInput: $("audio-input"),
        dropzone: $("dropzone"),
        selectedFile: $("selected-file"),
        fileType: $("file-type"),
        fileName: $("file-name"),
        fileMeta: $("file-meta"),
        removeFile: $("remove-file"),
        audioPreview: $("audio-preview"),
        captureOptions: $("capture-options"),
        recordButton: $("record-button"),
        recordButtonText: $("record-button-text"),
        recordingPanel: $("recording-panel"),
        recordingTimer: $("recording-timer"),
        stopRecordingButton: $("stop-recording-button"),
        analyzeButton: $("analyze-button"),
        uploadLoadingText: $("upload-loading-text"),
        fileError: $("file-error"),
        fileResult: $("file-result"),
        fileScoreRing: $("file-score-ring"),
        fileScore: $("file-score"),
        fileVerdict: $("file-verdict"),
        fileResultTitle: $("file-result-title"),
        fileSummary: $("file-summary"),
        metricConfidence: $("metric-confidence"),
        metricSpread: $("metric-spread"),
        metricQuality: $("metric-quality"),
        metricDuration: $("metric-duration"),
        metricWindows: $("metric-windows"),
        metricFormat: $("metric-format"),
        metricProcessing: $("metric-processing"),
        metricPitch: $("metric-pitch"),
        metricPauses: $("metric-pauses"),
        metricPitchVariation: $("metric-pitch-variation"),
        scenarioInput: $("scenario-input"),
        callOriginInput: $("call-origin-input"),
        languageInput: $("language-input"),
        urgencyInput: $("urgency-input"),
        sensitiveInput: $("sensitive-input"),
        beneficiaryInput: $("beneficiary-input"),
        referenceInput: $("reference-input"),
        demoPresetButton: $("demo-preset-button"),
        demoResetButton: $("demo-reset-button"),
        workflowPanel: $("workflow-panel"),
        workflowScore: $("workflow-score"),
        workflowLevel: $("workflow-level"),
        workflowAction: $("workflow-action"),
        workflowReasons: $("workflow-reasons"),
        workflowAlerts: $("workflow-alerts"),
        integrationStatus: $("integration-status"),
        speakerPanel: $("speaker-panel"),
        speakerResult: $("speaker-result"),
        speakerWarning: $("speaker-warning"),
        reliabilityChip: $("reliability-chip"),
        windowTimeline: $("window-timeline"),
        fileFingerprint: $("file-fingerprint"),
        evidenceChain: $("evidence-chain"),
        downloadReport: $("download-report"),
        copySummary: $("copy-summary"),
        historyList: $("history-list"),
        historyEmpty: $("history-empty"),
        clearHistory: $("clear-history"),
        feedbackPanel: $("feedback-panel"),
        feedbackStatus: $("feedback-status"),
        liveButton: $("live-button"),
        liveChip: $("live-chip"),
        liveStateText: $("live-state-text"),
        liveTimer: $("live-timer"),
        waveform: $("waveform"),
        micLevel: document.querySelector(".mic-level"),
        micLevelBar: $("mic-level-bar"),
        liveVerdict: $("live-verdict"),
        liveScore: $("live-score"),
        liveSummary: $("live-summary"),
        liveError: $("live-error"),
        installButtons: [...document.querySelectorAll(".install-button")],
        platformTip: $("platform-tip"),
    };

    const uploadState = {
        file: null,
        previewUrl: null,
        controller: null,
        modelReady: false,
        lastReport: null,
    };

    const liveState = {
        running: false,
        starting: false,
        stopping: false,
        socket: null,
        stream: null,
        context: null,
        source: null,
        processor: null,
        silentGain: null,
        timerId: null,
        startedAt: 0,
        recentScores: [],
        resampleState: { buffer: new Float32Array(0), position: 0 },
    };

    const recordState = {
        active: false,
        starting: false,
        recorder: null,
        stream: null,
        chunks: [],
        timerId: null,
        startedAt: 0,
        mimeType: "",
        extension: "webm",
    };

    let deferredInstallPrompt = null;
    const HISTORY_KEY = "voiceguard-scan-history-v1";
    const FEEDBACK_KEY = "voiceguard-result-feedback-v1";

    function formatBytes(bytes) {
        if (!Number.isFinite(bytes)) return "Unknown size";
        if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
        return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
    }

    function formatDuration(seconds) {
        if (!Number.isFinite(seconds) || seconds < 0) return "";
        const rounded = Math.round(seconds);
        const minutes = Math.floor(rounded / 60);
        return `${minutes}:${String(rounded % 60).padStart(2, "0")}`;
    }

    function getExtension(filename) {
        return filename.includes(".") ? filename.split(".").pop().toLowerCase() : "";
    }

    function showMessage(element, message) {
        element.textContent = message;
        element.hidden = false;
        element.focus({ preventScroll: true });
    }

    function hideMessage(element) {
        element.hidden = true;
        element.textContent = "";
    }

    function hasAudioConsent(errorElement) {
        if (elements.consentCheckbox.checked) return true;
        showMessage(
            errorElement,
            "Confirm that you have permission to analyze this audio before continuing."
        );
        elements.consentCheckbox.focus({ preventScroll: false });
        return false;
    }

    function setButtonLoading(button, loading) {
        const label = button.querySelector(".button-label");
        const loadingLabel = button.querySelector(".button-loading");
        if (label) label.hidden = loading;
        if (loadingLabel) loadingLabel.hidden = !loading;
        button.setAttribute("aria-busy", String(loading));
    }

    function resultTone(status, score) {
        const normalized = String(status || "").toUpperCase();
        if (normalized.includes("SYNTHETIC") || (Number.isFinite(score) && score >= 70)) {
            return { className: "synthetic", color: "#ff667e" };
        }
        if (normalized.includes("UNCERTAIN") || (Number.isFinite(score) && score >= 40)) {
            return { className: "uncertain", color: "#ffbd5c" };
        }
        if (normalized.includes("HUMAN")) {
            return { className: "human", color: "#3ee6a1" };
        }
        return { className: "neutral", color: "#6b8098" };
    }

    async function parseError(response) {
        try {
            const payload = await response.json();
            return payload?.detail?.message
                || payload?.error?.message
                || (typeof payload?.detail === "string" ? payload.detail : null)
                || `Request failed with status ${response.status}.`;
        } catch {
            return `Request failed with status ${response.status}.`;
        }
    }

    async function checkService() {
        try {
            const response = await fetch("/api/health", {
                headers: { Accept: "application/json" },
                cache: "no-store",
            });
            if (!response.ok) throw new Error("Health check failed");
            const payload = await response.json();
            uploadState.modelReady = Boolean(payload?.model?.ready);
            elements.dashboardApi.textContent = "Online";
            elements.dashboardModel.textContent = uploadState.modelReady ? "Ready" : "Warming up";
            elements.dashboardModelName.textContent = String(payload?.model?.model || "Pinned Wav2Vec2").split("/").pop();
            elements.dashboardDevice.textContent = String(payload?.model?.device || "CPU").toUpperCase();
            elements.dashboardTransport.textContent = window.isSecureContext ? "HTTPS + WSS" : "Local HTTP + WS";
            elements.dashboardVersion.textContent = payload?.version || "2.2.0";
            elements.dashboardAudit.textContent = payload?.audit_store?.chain_valid
                ? `Verified · ${payload.audit_store.chain_records} records`
                : "Integrity check failed";
            elements.serviceState.classList.add("online");
            elements.serviceState.classList.remove("offline");
            elements.serviceStateText.textContent = uploadState.modelReady
                ? "Detector ready"
                : "Service online";
            elements.serviceState.title = uploadState.modelReady
                ? "Detector model is loaded"
                : "The detector model will load during the first analysis";
        } catch {
            elements.dashboardApi.textContent = "Offline";
            elements.dashboardModel.textContent = "Unavailable";
            elements.dashboardDevice.textContent = "—";
            elements.dashboardTransport.textContent = "Disconnected";
            elements.dashboardAudit.textContent = "Unavailable";
            elements.serviceState.classList.add("offline");
            elements.serviceState.classList.remove("online");
            elements.serviceStateText.textContent = "Service offline";
            elements.serviceState.title = "Start the VoiceGuard server to analyze audio";
        }
    }

    function clearSelectedFile() {
        if (uploadState.controller) {
            uploadState.controller.abort();
            uploadState.controller = null;
        }
        if (uploadState.previewUrl) {
            URL.revokeObjectURL(uploadState.previewUrl);
            uploadState.previewUrl = null;
        }
        uploadState.file = null;
        elements.audioInput.value = "";
        elements.audioPreview.removeAttribute("src");
        elements.selectedFile.hidden = true;
        elements.dropzone.hidden = false;
        elements.captureOptions.hidden = false;
        elements.analyzeButton.disabled = true;
        elements.fileResult.hidden = true;
        hideMessage(elements.fileError);
        setButtonLoading(elements.analyzeButton, false);
    }

    function selectFile(file) {
        hideMessage(elements.fileError);
        elements.fileResult.hidden = true;

        const extension = getExtension(file?.name || "");
        if (!file || !ALLOWED_EXTENSIONS.has(extension)) {
            showMessage(
                elements.fileError,
                "Choose a WAV, MP3, M4A, WebM, AAC, FLAC, OGG, or OPUS recording."
            );
            return;
        }
        if (file.size <= 0) {
            showMessage(elements.fileError, "This recording is empty.");
            return;
        }
        if (file.size > MAX_FILE_BYTES) {
            showMessage(elements.fileError, "The recording must be 25 MB or smaller.");
            return;
        }

        if (uploadState.previewUrl) URL.revokeObjectURL(uploadState.previewUrl);
        uploadState.file = file;
        uploadState.previewUrl = URL.createObjectURL(file);
        elements.audioPreview.src = uploadState.previewUrl;
        elements.fileType.textContent = extension.toUpperCase().slice(0, 4);
        elements.fileName.textContent = file.name;
        elements.fileMeta.textContent = `${formatBytes(file.size)} · Reading duration…`;
        elements.dropzone.hidden = true;
        elements.captureOptions.hidden = true;
        elements.selectedFile.hidden = false;
        elements.analyzeButton.disabled = false;

        elements.audioPreview.onloadedmetadata = () => {
            const duration = elements.audioPreview.duration;
            const durationText = Number.isFinite(duration) ? ` · ${formatDuration(duration)}` : "";
            elements.fileMeta.textContent = `${formatBytes(file.size)}${durationText} · Ready to analyze`;
        };
        elements.audioPreview.onerror = () => {
            elements.fileMeta.textContent = `${formatBytes(file.size)} · Server will verify this format`;
        };
    }

    function reliabilityFor(analysis) {
        if (!Number.isFinite(analysis.risk_score)) return { label: "No verdict", className: "neutral" };
        if (analysis.audio_quality?.quality !== "good") return { label: "Limited audio", className: "uncertain" };
        if (Number(analysis.score_spread) >= 35) return { label: "Mixed evidence", className: "uncertain" };
        return { label: "Consistent windows", className: "strong" };
    }

    function renderTimeline(scores) {
        elements.windowTimeline.replaceChildren();
        if (!Array.isArray(scores) || scores.length === 0) {
            const empty = document.createElement("span");
            empty.className = "timeline-empty";
            empty.textContent = "No speech windows were scored.";
            elements.windowTimeline.append(empty);
            return;
        }
        scores.forEach((rawScore, index) => {
            const score = Math.max(0, Math.min(100, Number(rawScore) || 0));
            const item = document.createElement("div");
            item.className = "timeline-item";
            const track = document.createElement("span");
            track.className = "timeline-track";
            const fill = document.createElement("i");
            fill.style.width = `${score}%`;
            track.append(fill);
            const label = document.createElement("small");
            label.textContent = `Window ${index + 1}`;
            const value = document.createElement("strong");
            value.textContent = `${Math.round(score)}%`;
            item.append(track, label, value);
            elements.windowTimeline.append(item);
        });
    }

    function readHistory() {
        try {
            const value = JSON.parse(localStorage.getItem(HISTORY_KEY) || "[]");
            return Array.isArray(value) ? value : [];
        } catch {
            return [];
        }
    }

    function renderHistory() {
        const history = readHistory();
        elements.historyList.replaceChildren();
        elements.historyEmpty.hidden = history.length > 0;
        history.forEach((item) => {
            const row = document.createElement("article");
            row.className = "history-item";
            const identity = document.createElement("div");
            const name = document.createElement("strong");
            name.textContent = item.filename;
            const detail = document.createElement("span");
            detail.textContent = `${new Date(item.analyzedAt).toLocaleString()} · ${item.fingerprint}`;
            identity.append(name, detail);
            const result = document.createElement("div");
            const verdict = document.createElement("span");
            const tone = resultTone(item.status, item.score);
            verdict.className = `history-verdict ${tone.className}`;
            verdict.textContent = item.status;
            const score = document.createElement("strong");
            score.style.color = tone.color;
            score.textContent = Number.isFinite(item.score) ? `${Math.round(item.score)}%` : "—";
            result.append(verdict, score);
            row.append(identity, result);
            elements.historyList.append(row);
        });
    }

    function saveHistory(report) {
        const history = readHistory();
        history.unshift({
            filename: report.filename,
            status: report.analysis.status,
            score: report.analysis.risk_score,
            analyzedAt: report.metadata.analyzed_at || new Date().toISOString(),
            fingerprint: report.metadata.sha256?.slice(0, 12) || "no fingerprint",
        });
        try {
            localStorage.setItem(HISTORY_KEY, JSON.stringify(history.slice(0, 6)));
        } catch {
            return;
        }
        renderHistory();
    }

    function renderSecurityWorkflow(workflow, comparison, integrations) {
        elements.workflowPanel.hidden = !workflow;
        if (workflow) {
            const score = Number(workflow.combined_risk_score);
            const tone = resultTone(workflow.risk_level, score);
            elements.workflowScore.textContent = Number.isFinite(score) ? `${Math.round(score)}%` : "—";
            elements.workflowScore.style.color = tone.color;
            elements.workflowLevel.className = `verdict ${tone.className}`;
            elements.workflowLevel.textContent = workflow.risk_level || "ASSESSED";
            elements.workflowAction.textContent = workflow.recommended_action || "Review the result.";
            elements.workflowReasons.replaceChildren();
            (workflow.reasons || []).forEach((reason) => {
                const item = document.createElement("li");
                item.textContent = reason;
                elements.workflowReasons.append(item);
            });
            elements.workflowAlerts.replaceChildren();
            (workflow.alerts || []).forEach((alert) => {
                const item = document.createElement("span");
                item.textContent = alert;
                elements.workflowAlerts.append(item);
            });
            const delivered = (integrations?.deliveries || [])
                .filter((item) => item.status === "delivered")
                .map((item) => item.channel);
            const failed = (integrations?.deliveries || [])
                .filter((item) => item.status === "failed")
                .map((item) => item.channel);
            if (delivered.length) {
                elements.integrationStatus.textContent = `Real external alert delivered through: ${delivered.join(", ")}. Metadata only; no audio was shared.`;
            } else if (failed.length) {
                elements.integrationStatus.textContent = `External delivery failed for: ${failed.join(", ")}. Analysis and local audit remain available.`;
            } else {
                elements.integrationStatus.textContent = integrations?.audit_recorded
                    ? "Incident metadata saved to the local audit database. External alerts are not configured."
                    : "External alerts are not configured; displayed notification actions are simulations.";
            }
        }

        elements.speakerPanel.hidden = !comparison;
        if (comparison) {
            elements.speakerResult.textContent = `${comparison.assessment} · ${comparison.similarity_score}%`;
            elements.speakerWarning.textContent = comparison.warning || "Experimental comparison only.";
        }
    }

    function renderFileResult(analysis, filename, metadata = {}, extras = {}) {
        const score = Number.isFinite(analysis.risk_score) ? analysis.risk_score : null;
        const tone = resultTone(analysis.status, score);
        elements.fileScore.textContent = score === null ? "—" : `${Math.round(score)}%`;
        elements.fileScoreRing.style.setProperty("--score", score ?? 0);
        elements.fileScoreRing.style.setProperty("--ring-color", tone.color);
        elements.fileScoreRing.setAttribute(
            "aria-label",
            score === null ? "No synthetic voice risk score" : `${score} percent synthetic voice risk`
        );
        elements.fileVerdict.className = `verdict ${tone.className}`;
        elements.fileVerdict.textContent = analysis.status || "Analysis complete";
        elements.fileResultTitle.textContent = score === null
            ? "More speech needed"
            : filename || "Analysis result";
        elements.fileSummary.textContent = analysis.summary || "The recording has been screened.";
        elements.metricConfidence.textContent = Number.isFinite(analysis.confidence_score)
            ? `${analysis.confidence_score}%`
            : "Not available";
        elements.metricSpread.textContent = Number.isFinite(analysis.score_spread)
            ? `${analysis.score_spread}%`
            : "Not available";
        elements.metricQuality.textContent = String(
            analysis.audio_quality?.quality || "unknown"
        ).replaceAll("_", " ");
        elements.metricDuration.textContent = Number.isFinite(analysis.audio_quality?.duration_seconds)
            ? formatDuration(analysis.audio_quality.duration_seconds)
            : "—";
        elements.metricWindows.textContent = String(analysis.analysis_windows ?? "—");
        elements.metricFormat.textContent = getExtension(filename || "").toUpperCase() || "Unknown";
        elements.metricProcessing.textContent = Number.isFinite(metadata.processing_ms)
            ? `${(metadata.processing_ms / 1000).toFixed(2)} seconds`
            : "Not available";
        elements.metricPitch.textContent = Number.isFinite(analysis.dsp_metrics?.pitch_median_hz)
            ? `${analysis.dsp_metrics.pitch_median_hz} Hz`
            : "Not available";
        elements.metricPauses.textContent = Number.isFinite(analysis.dsp_metrics?.pause_ratio)
            ? `${Math.round(analysis.dsp_metrics.pause_ratio * 100)}%`
            : "Not available";
        elements.metricPitchVariation.textContent = Number.isFinite(analysis.dsp_metrics?.pitch_variation)
            ? `${Math.round(analysis.dsp_metrics.pitch_variation * 100)}%`
            : "Not available";
        const reliability = reliabilityFor(analysis);
        elements.reliabilityChip.className = `reliability-chip ${reliability.className}`;
        elements.reliabilityChip.textContent = reliability.label;
        elements.fileFingerprint.textContent = metadata.sha256
            ? `${metadata.sha256.slice(0, 16)}…`
            : "Unavailable";
        const chainHash = extras.externalIntegrations?.evidence_chain?.record_hash;
        elements.evidenceChain.textContent = chainHash
            ? `${chainHash.slice(0, 16)}… · ${extras.externalIntegrations.chain_verified ? "verified" : "check failed"}`
            : "Audit unavailable";
        renderTimeline(analysis.window_scores);
        renderSecurityWorkflow(extras.securityWorkflow, extras.speakerComparison, extras.externalIntegrations);
        uploadState.lastReport = {
            report_type: "VoiceGuard screening evidence",
            filename,
            metadata,
            reliability: reliability.label,
            capture_warning: "Replay, room acoustics, codecs, and unseen voice generators can change detection accuracy.",
            analysis,
            context: extras.context || {},
            speaker_comparison: extras.speakerComparison || null,
            security_workflow: extras.securityWorkflow || null,
            external_integrations: extras.externalIntegrations || null,
        };
        saveHistory(uploadState.lastReport);
        elements.feedbackStatus.textContent = "Feedback stays on this device and never includes audio.";
        elements.feedbackPanel.querySelectorAll("button").forEach((button) => {
            button.classList.remove("selected");
        });
        elements.fileResult.hidden = false;
        elements.fileResult.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }

    async function analyzeSelectedFile(event) {
        event.preventDefault();
        if (!uploadState.file || uploadState.controller) return;
        if (!hasAudioConsent(elements.fileError)) return;

        hideMessage(elements.fileError);
        elements.fileResult.hidden = true;
        uploadState.controller = new AbortController();
        elements.analyzeButton.disabled = true;
        elements.uploadLoadingText.textContent = uploadState.modelReady
            ? "Analyzing voice…"
            : "Preparing model & analyzing…";
        setButtonLoading(elements.analyzeButton, true);

        const body = new FormData();
        body.append("file", uploadState.file);
        body.append("scenario", elements.scenarioInput.value);
        body.append("call_origin", elements.callOriginInput.value);
        body.append("language", elements.languageInput.value);
        body.append("urgency", String(elements.urgencyInput.checked));
        body.append("sensitive_request", String(elements.sensitiveInput.checked));
        body.append("new_beneficiary", String(elements.beneficiaryInput.checked));
        if (elements.referenceInput.files[0]) {
            body.append("reference", elements.referenceInput.files[0]);
        }

        try {
            const response = await fetch("/api/analyze-file", {
                method: "POST",
                body,
                signal: uploadState.controller.signal,
            });
            if (!response.ok) throw new Error(await parseError(response));
            const payload = await response.json();
            uploadState.modelReady = true;
            renderFileResult(payload.analysis, payload.filename, payload.analysis_metadata || {}, {
                context: payload.context,
                securityWorkflow: payload.security_workflow,
                speakerComparison: payload.speaker_comparison,
                externalIntegrations: payload.external_integrations,
            });
            elements.serviceStateText.textContent = "Detector ready";
            elements.serviceState.title = "Detector model is loaded";
        } catch (error) {
            if (error.name !== "AbortError") {
                showMessage(
                    elements.fileError,
                    error.message || "The recording could not be analyzed. Please try again."
                );
            }
        } finally {
            uploadState.controller = null;
            setButtonLoading(elements.analyzeButton, false);
            elements.analyzeButton.disabled = !uploadState.file;
        }
    }

    function preferredRecorderFormat() {
        const formats = [
            { mimeType: "audio/webm;codecs=opus", extension: "webm" },
            { mimeType: "audio/webm", extension: "webm" },
            { mimeType: "audio/mp4", extension: "m4a" },
            { mimeType: "audio/ogg;codecs=opus", extension: "ogg" },
        ];
        return formats.find((format) => MediaRecorder.isTypeSupported(format.mimeType))
            || { mimeType: "", extension: "webm" };
    }

    function updateRecordingTimer() {
        const elapsed = Math.max(0, Math.floor((Date.now() - recordState.startedAt) / 1000));
        elements.recordingTimer.textContent = formatDuration(elapsed);
        elements.recordingTimer.dateTime = `PT${elapsed}S`;
        if (elapsed >= 60) stopRecording();
    }

    function releaseRecordingResources() {
        if (recordState.timerId) {
            window.clearInterval(recordState.timerId);
            recordState.timerId = null;
        }
        if (recordState.stream) {
            recordState.stream.getTracks().forEach((track) => track.stop());
            recordState.stream = null;
        }
        recordState.active = false;
        recordState.starting = false;
        recordState.recorder = null;
        elements.recordButton.disabled = !window.MediaRecorder;
        elements.recordingPanel.hidden = true;
        elements.captureOptions.hidden = Boolean(uploadState.file);
        elements.dropzone.hidden = Boolean(uploadState.file);
        elements.recordingTimer.textContent = "00:00";
        elements.recordingTimer.dateTime = "PT0S";
    }

    async function startRecording() {
        if (!hasAudioConsent(elements.fileError)) return;
        if (recordState.active || recordState.starting) return;
        hideMessage(elements.fileError);
        if (liveState.running || liveState.starting) {
            showMessage(elements.fileError, "Stop live monitoring before recording a new sample.");
            return;
        }
        if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
            showMessage(
                elements.fileError,
                "Microphone recording requires HTTPS or localhost in a supported browser."
            );
            return;
        }
        if (!window.MediaRecorder) {
            showMessage(elements.fileError, "This browser does not support microphone recording.");
            return;
        }

        recordState.starting = true;
        elements.recordButton.disabled = true;
        try {
            recordState.stream = await navigator.mediaDevices.getUserMedia({
                audio: {
                    channelCount: { ideal: 1 },
                    echoCancellation: false,
                    noiseSuppression: false,
                    autoGainControl: false,
                },
                video: false,
            });
            const format = preferredRecorderFormat();
            const options = format.mimeType ? { mimeType: format.mimeType } : undefined;
            const recorder = new MediaRecorder(recordState.stream, options);
            recordState.recorder = recorder;
            recordState.mimeType = recorder.mimeType || format.mimeType || "audio/webm";
            recordState.extension = format.extension;
            recordState.chunks = [];

            recorder.ondataavailable = (event) => {
                if (event.data?.size) recordState.chunks.push(event.data);
            };
            recorder.onerror = () => {
                recorder.onstop = null;
                showMessage(elements.fileError, "The browser could not finish this recording.");
                releaseRecordingResources();
            };
            recorder.onstop = () => {
                const chunks = recordState.chunks;
                const mimeType = recordState.mimeType;
                const extension = recordState.extension;
                releaseRecordingResources();
                const blob = new Blob(chunks, { type: mimeType });
                if (!blob.size) {
                    showMessage(elements.fileError, "No microphone audio was recorded. Please try again.");
                    return;
                }
                const file = new File(
                    [blob],
                    `voiceguard-recording-${Date.now()}.${extension}`,
                    { type: mimeType, lastModified: Date.now() }
                );
                selectFile(file);
            };

            recordState.active = true;
            recordState.starting = false;
            recordState.startedAt = Date.now();
            elements.captureOptions.hidden = true;
            elements.dropzone.hidden = true;
            elements.recordingPanel.hidden = false;
            recorder.start(500);
            recordState.timerId = window.setInterval(updateRecordingTimer, 1000);
            updateRecordingTimer();
        } catch (error) {
            releaseRecordingResources();
            const denied = error?.name === "NotAllowedError";
            showMessage(
                elements.fileError,
                denied
                    ? "Microphone permission was denied. Allow it in browser settings and try again."
                    : (error.message || "Microphone recording could not start.")
            );
        }
    }

    function stopRecording() {
        if (recordState.recorder?.state === "recording") {
            elements.stopRecordingButton.disabled = true;
            recordState.recorder.addEventListener(
                "stop",
                () => { elements.stopRecordingButton.disabled = false; },
                { once: true }
            );
            recordState.recorder.stop();
        }
    }

    function discardRecording() {
        if (recordState.recorder) {
            recordState.recorder.onstop = null;
            if (recordState.recorder.state !== "inactive") recordState.recorder.stop();
        }
        releaseRecordingResources();
    }

    function createWaveform() {
        const fragment = document.createDocumentFragment();
        for (let index = 0; index < 38; index += 1) {
            fragment.appendChild(document.createElement("span"));
        }
        elements.waveform.appendChild(fragment);
    }

    function updateMicVisual(pcm) {
        let sum = 0;
        for (let index = 0; index < pcm.length; index += 1) {
            const value = pcm[index] / 32768;
            sum += value * value;
        }
        const rms = Math.sqrt(sum / Math.max(1, pcm.length));
        const level = Math.min(100, Math.max(0, (20 * Math.log10(Math.max(rms, 0.0001)) + 60) * 1.8));
        elements.micLevelBar.style.width = `${level}%`;
        elements.micLevel.setAttribute("aria-valuenow", String(Math.round(level)));

        const bars = elements.waveform.children;
        const time = performance.now() / 150;
        for (let index = 0; index < bars.length; index += 1) {
            const shape = 0.24 + 0.76 * Math.abs(Math.sin(index * 0.71 + time));
            const height = 4 + level * 0.58 * shape;
            bars[index].style.height = `${Math.min(62, height)}px`;
        }
    }

    function sendPcm(pcm) {
        if (!(pcm instanceof Int16Array) || pcm.length === 0) return;
        updateMicVisual(pcm);
        const socket = liveState.socket;
        if (
            socket?.readyState === WebSocket.OPEN
            && socket.bufferedAmount < WS_BACKPRESSURE_LIMIT
        ) {
            socket.send(pcm.buffer);
        }
    }

    function resampleForFallback(input, sourceRate) {
        if (sourceRate === TARGET_SAMPLE_RATE) {
            const pcm = new Int16Array(input.length);
            for (let index = 0; index < input.length; index += 1) {
                const sample = Math.max(-1, Math.min(1, input[index]));
                pcm[index] = sample < 0 ? sample * 32768 : sample * 32767;
            }
            return pcm;
        }

        const prior = liveState.resampleState.buffer;
        const combined = new Float32Array(prior.length + input.length);
        combined.set(prior);
        combined.set(input, prior.length);
        const ratio = sourceRate / TARGET_SAMPLE_RATE;
        let position = liveState.resampleState.position;
        const output = [];

        while (position + 1 < combined.length) {
            const left = Math.floor(position);
            const fraction = position - left;
            const sample = combined[left] * (1 - fraction) + combined[left + 1] * fraction;
            output.push(Math.max(-1, Math.min(1, sample)));
            position += ratio;
        }

        const consumed = Math.floor(position);
        liveState.resampleState.buffer = combined.slice(consumed);
        liveState.resampleState.position = position - consumed;
        const pcm = new Int16Array(output.length);
        for (let index = 0; index < output.length; index += 1) {
            pcm[index] = output[index] < 0 ? output[index] * 32768 : output[index] * 32767;
        }
        return pcm;
    }

    async function setupAudioGraph() {
        const AudioContextClass = window.AudioContext || window.webkitAudioContext;
        if (!AudioContextClass) {
            throw new Error("This browser does not support live audio processing.");
        }

        liveState.context = new AudioContextClass({ latencyHint: "interactive" });
        await liveState.context.resume();
        liveState.source = liveState.context.createMediaStreamSource(liveState.stream);
        liveState.silentGain = liveState.context.createGain();
        liveState.silentGain.gain.value = 0;
        liveState.silentGain.connect(liveState.context.destination);

        if (liveState.context.audioWorklet && window.AudioWorkletNode) {
            await liveState.context.audioWorklet.addModule("/static/pcm-worklet.js");
            liveState.processor = new AudioWorkletNode(
                liveState.context,
                "voiceguard-pcm-processor",
                { processorOptions: { targetSampleRate: TARGET_SAMPLE_RATE } }
            );
            liveState.processor.port.onmessage = (event) => {
                if (event.data?.type === "pcm") sendPcm(event.data.samples);
            };
        } else {
            const processor = liveState.context.createScriptProcessor(4096, 1, 1);
            processor.onaudioprocess = (event) => {
                const input = event.inputBuffer.getChannelData(0);
                sendPcm(resampleForFallback(input, liveState.context.sampleRate));
            };
            liveState.processor = processor;
        }

        liveState.source.connect(liveState.processor);
        liveState.processor.connect(liveState.silentGain);
    }

    function openSocket() {
        return new Promise((resolve, reject) => {
            const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
            const socket = new WebSocket(`${protocol}//${window.location.host}/ws/stream`);
            socket.binaryType = "arraybuffer";
            liveState.socket = socket;
            const timeout = window.setTimeout(() => {
                reject(new Error("The detector server took too long to connect."));
                socket.close();
            }, 15000);

            socket.onopen = () => {
                window.clearTimeout(timeout);
                socket.send(JSON.stringify({
                    type: "start",
                    format: "pcm_s16le",
                    sample_rate: TARGET_SAMPLE_RATE,
                    channels: 1,
                }));
                resolve();
            };
            socket.onerror = () => {
                window.clearTimeout(timeout);
                reject(new Error("Could not connect to the live detector."));
            };
            socket.onmessage = handleLiveMessage;
            socket.onclose = () => {
                window.clearTimeout(timeout);
                if (liveState.running && !liveState.stopping) {
                    stopLive("The live detector disconnected. Please start it again.");
                }
            };
        });
    }

    function setLiveChip(active, label) {
        elements.liveChip.classList.toggle("active", active);
        const text = elements.liveChip.querySelector("span");
        if (text) text.textContent = label;
    }

    function setLiveConnecting(connecting, message = "Requesting microphone") {
        liveState.starting = connecting;
        elements.liveButton.disabled = connecting;
        setButtonLoading(elements.liveButton, connecting);
        elements.liveStateText.textContent = message;
        setLiveChip(false, connecting ? "Connecting" : "Standby");
    }

    function updateTimer() {
        const elapsed = Math.max(0, Math.floor((Date.now() - liveState.startedAt) / 1000));
        elements.liveTimer.textContent = formatDuration(elapsed);
        elements.liveTimer.dateTime = `PT${elapsed}S`;
    }

    function renderLiveResult(payload) {
        if (!Number.isFinite(payload.risk_score)) {
            liveState.recentScores = [];
            elements.liveScore.textContent = "—";
            elements.liveVerdict.textContent = "Waiting for clear speech";
            elements.liveSummary.textContent = payload.summary || "Move closer to the speaker and try again.";
            return;
        }

        liveState.recentScores.push(payload.risk_score);
        if (liveState.recentScores.length > 3) liveState.recentScores.shift();
        const score = liveState.recentScores.reduce((sum, value) => sum + value, 0)
            / liveState.recentScores.length;
        let status;
        if (score >= 70) status = "LIKELY SYNTHETIC";
        else if (score >= 40) status = "UNCERTAIN";
        else status = "LIKELY HUMAN";
        const tone = resultTone(status, score);

        elements.liveScore.textContent = `${Math.round(score)}%`;
        elements.liveScore.style.color = tone.color;
        elements.liveVerdict.textContent = status;
        elements.liveVerdict.style.color = tone.color;
        elements.liveSummary.textContent = payload.summary || "Rolling analysis updated.";
    }

    function handleLiveMessage(event) {
        let payload;
        try {
            payload = JSON.parse(event.data);
        } catch {
            showMessage(elements.liveError, "The detector returned an invalid live response.");
            return;
        }

        if (payload.type === "state") {
            elements.liveStateText.textContent = payload.message || payload.state;
            if (payload.state === "loading_model") {
                setLiveChip(true, "Loading model");
            }
            return;
        }
        if (payload.type === "error") {
            const message = payload.error?.message || "Live analysis could not continue.";
            if (payload.error?.code === "invalid_audio") {
                elements.liveSummary.textContent = message;
            } else {
                showMessage(elements.liveError, message);
            }
            return;
        }
        if (payload.type === "result") {
            elements.liveStateText.textContent = "Rolling five-second analysis";
            setLiveChip(true, "Listening");
            renderLiveResult(payload);
        }
    }

    async function startLive() {
        if (liveState.running || liveState.starting) return;
        hideMessage(elements.liveError);
        if (!hasAudioConsent(elements.liveError)) return;
        if (recordState.active || recordState.starting) {
            showMessage(elements.liveError, "Finish the microphone recording before starting live monitoring.");
            return;
        }
        liveState.recentScores = [];
        liveState.resampleState = { buffer: new Float32Array(0), position: 0 };
        setLiveConnecting(true);

        if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
            setLiveConnecting(false);
            showMessage(
                elements.liveError,
                "Microphone access requires HTTPS or localhost in a supported browser."
            );
            return;
        }

        try {
            liveState.stream = await navigator.mediaDevices.getUserMedia({
                audio: {
                    channelCount: { ideal: 1 },
                    echoCancellation: false,
                    noiseSuppression: false,
                    autoGainControl: false,
                },
                video: false,
            });
            elements.liveStateText.textContent = "Preparing secure audio stream";
            await setupAudioGraph();
            await openSocket();

            liveState.running = true;
            liveState.startedAt = Date.now();
            liveState.timerId = window.setInterval(updateTimer, 1000);
            updateTimer();
            setLiveConnecting(false);
            elements.liveButton.disabled = false;
            elements.liveButton.classList.add("running");
            elements.liveButton.setAttribute("aria-pressed", "true");
            elements.liveButton.querySelector(".button-label").innerHTML =
                '<span class="record-dot" aria-hidden="true"></span> Stop live monitoring';
            elements.waveform.classList.add("active");
            elements.liveStateText.textContent = "Listening for a clear speech sample";
            elements.liveVerdict.textContent = "Collecting audio";
            elements.liveSummary.textContent = "Rolling results begin after the detector is ready and five seconds of clear audio is collected.";
            setLiveChip(true, "Listening");
        } catch (error) {
            await stopLive();
            const denied = error?.name === "NotAllowedError";
            showMessage(
                elements.liveError,
                denied
                    ? "Microphone permission was denied. Allow access in your browser settings and try again."
                    : (error.message || "Live microphone monitoring could not start.")
            );
        }
    }

    async function stopLive(errorMessage = "") {
        if (liveState.stopping) return;
        liveState.stopping = true;
        const wasActive = liveState.running || liveState.starting;
        liveState.running = false;
        liveState.starting = false;

        if (liveState.timerId) {
            window.clearInterval(liveState.timerId);
            liveState.timerId = null;
        }

        const socket = liveState.socket;
        liveState.socket = null;
        if (socket) {
            socket.onclose = null;
            socket.onmessage = null;
            socket.onerror = null;
            if (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING) {
                socket.close(1000, "User stopped monitoring");
            }
        }

        if (liveState.processor) {
            if (liveState.processor.port) liveState.processor.port.onmessage = null;
            if ("onaudioprocess" in liveState.processor) liveState.processor.onaudioprocess = null;
            try { liveState.processor.disconnect(); } catch {}
            liveState.processor = null;
        }
        if (liveState.source) {
            try { liveState.source.disconnect(); } catch {}
            liveState.source = null;
        }
        if (liveState.silentGain) {
            try { liveState.silentGain.disconnect(); } catch {}
            liveState.silentGain = null;
        }
        if (liveState.stream) {
            liveState.stream.getTracks().forEach((track) => track.stop());
            liveState.stream = null;
        }
        if (liveState.context) {
            try { await liveState.context.close(); } catch {}
            liveState.context = null;
        }

        elements.liveButton.disabled = false;
        elements.liveButton.classList.remove("running");
        elements.liveButton.setAttribute("aria-pressed", "false");
        setButtonLoading(elements.liveButton, false);
        elements.liveButton.querySelector(".button-label").innerHTML =
            '<span class="record-dot" aria-hidden="true"></span> Start live microphone';
        elements.waveform.classList.remove("active");
        [...elements.waveform.children].forEach((bar) => { bar.style.height = "4px"; });
        elements.micLevelBar.style.width = "0%";
        elements.micLevel.setAttribute("aria-valuenow", "0");
        elements.liveStateText.textContent = errorMessage ? "Monitoring stopped" : "Ready when you are";
        setLiveChip(false, "Standby");

        if (wasActive && !errorMessage) {
            elements.liveSummary.textContent = "Session stopped. Start again whenever you need another check.";
        }
        if (errorMessage) showMessage(elements.liveError, errorMessage);
        liveState.stopping = false;
    }

    async function toggleLive() {
        if (liveState.running) await stopLive();
        else await startLive();
    }

    function setupUploadEvents() {
        elements.audioInput.addEventListener("change", () => {
            const [file] = elements.audioInput.files;
            if (file) selectFile(file);
        });
        elements.removeFile.addEventListener("click", clearSelectedFile);
        elements.recordButton.addEventListener("click", startRecording);
        elements.stopRecordingButton.addEventListener("click", stopRecording);
        elements.uploadForm.addEventListener("submit", analyzeSelectedFile);
        elements.downloadReport.addEventListener("click", () => {
            if (!uploadState.lastReport) return;
            const reportBlob = new Blob(
                [JSON.stringify(uploadState.lastReport, null, 2)],
                { type: "application/json" }
            );
            const reportUrl = URL.createObjectURL(reportBlob);
            const link = document.createElement("a");
            link.href = reportUrl;
            link.download = `voiceguard-report-${Date.now()}.json`;
            link.click();
            URL.revokeObjectURL(reportUrl);
        });
        elements.copySummary.addEventListener("click", async () => {
            if (!uploadState.lastReport) return;
            const report = uploadState.lastReport;
            const score = report.analysis.risk_score ?? "no";
            const fingerprint = report.metadata.sha256 || "unavailable";
            const summary = `VoiceGuard: ${report.analysis.status} (${score}% AI risk). Reliability: ${report.reliability}. File fingerprint: ${fingerprint}. Screening only, not proof.`;
            try {
                await navigator.clipboard.writeText(summary);
                elements.copySummary.textContent = "Copied";
                window.setTimeout(() => { elements.copySummary.textContent = "Copy summary"; }, 1600);
            } catch {
                showMessage(elements.fileError, "Your browser blocked clipboard access.");
            }
        });

        for (const eventName of ["dragenter", "dragover"]) {
            elements.dropzone.addEventListener(eventName, (event) => {
                event.preventDefault();
                if (event.dataTransfer) event.dataTransfer.dropEffect = "copy";
                elements.dropzone.classList.add("dragging");
            });
        }
        for (const eventName of ["dragleave", "drop"]) {
            elements.dropzone.addEventListener(eventName, (event) => {
                event.preventDefault();
                elements.dropzone.classList.remove("dragging");
            });
        }
        elements.dropzone.addEventListener("drop", (event) => {
            const [file] = event.dataTransfer?.files || [];
            if (file) selectFile(file);
        });
    }

    function updatePlatformTip() {
        const standalone = window.matchMedia("(display-mode: standalone)").matches
            || window.navigator.standalone === true;
        const ios = /iphone|ipad|ipod/i.test(navigator.userAgent);
        const tip = elements.platformTip.querySelector("span");
        if (standalone) {
            tip.textContent = "VoiceGuard is installed. Keep it open in the foreground while monitoring.";
        } else if (ios) {
            tip.textContent = "On iPhone or iPad, tap Share, then “Add to Home Screen” to install.";
        }
    }

    function showInstallButtons(show) {
        elements.installButtons.forEach((button) => {
            button.hidden = !show;
        });
    }

    function setupPwa() {
        if ("serviceWorker" in navigator) {
            window.addEventListener("load", () => {
                navigator.serviceWorker.register("/sw.js").catch(() => {});
            });
        }

        window.addEventListener("beforeinstallprompt", (event) => {
            event.preventDefault();
            deferredInstallPrompt = event;
            showInstallButtons(true);
        });
        window.addEventListener("appinstalled", () => {
            deferredInstallPrompt = null;
            showInstallButtons(false);
            updatePlatformTip();
        });
        elements.installButtons.forEach((button) => {
            button.addEventListener("click", async () => {
                if (!deferredInstallPrompt) return;
                deferredInstallPrompt.prompt();
                await deferredInstallPrompt.userChoice;
                deferredInstallPrompt = null;
                showInstallButtons(false);
            });
        });
        updatePlatformTip();
    }

    function setupShowcase() {
        elements.demoPresetButton.addEventListener("click", () => {
            elements.scenarioInput.value = "fund_transfer";
            elements.callOriginInput.value = "spoofed";
            elements.languageInput.value = "english";
            elements.urgencyInput.checked = true;
            elements.sensitiveInput.checked = true;
            elements.beneficiaryInput.checked = true;
            elements.demoPresetButton.textContent = "High-risk scenario loaded";
            elements.demoPresetButton.disabled = true;
            elements.demoResetButton.hidden = false;
        });

        elements.demoResetButton.addEventListener("click", () => {
            elements.scenarioInput.value = "general";
            elements.callOriginInput.value = "known";
            elements.languageInput.value = "unspecified";
            elements.urgencyInput.checked = false;
            elements.sensitiveInput.checked = false;
            elements.beneficiaryInput.checked = false;
            elements.demoPresetButton.textContent = "Load high-risk scenario";
            elements.demoPresetButton.disabled = false;
            elements.demoResetButton.hidden = true;
        });

        elements.dashboardButton.addEventListener("click", () => {
            const enabled = !document.body.classList.contains("dashboard-mode");
            document.body.classList.toggle("dashboard-mode", enabled);
            elements.dashboardButton.setAttribute("aria-pressed", String(enabled));
            elements.dashboardButton.textContent = enabled ? "Exit dashboard view" : "Dashboard view";
            if (enabled) {
                elements.systemDashboard.scrollIntoView({ behavior: "smooth", block: "start" });
            }
        });

        elements.feedbackPanel.querySelectorAll("button[data-feedback]").forEach((button) => {
            button.addEventListener("click", () => {
                if (!uploadState.lastReport) return;
                const feedback = {
                    fingerprint: uploadState.lastReport.metadata.sha256?.slice(0, 16) || "unavailable",
                    result: uploadState.lastReport.analysis.status,
                    score: uploadState.lastReport.analysis.risk_score,
                    response: button.dataset.feedback,
                    recorded_at: new Date().toISOString(),
                };
                try {
                    const existing = JSON.parse(localStorage.getItem(FEEDBACK_KEY) || "[]");
                    const values = Array.isArray(existing) ? existing : [];
                    values.unshift(feedback);
                    localStorage.setItem(FEEDBACK_KEY, JSON.stringify(values.slice(0, 50)));
                } catch {}
                elements.feedbackPanel.querySelectorAll("button").forEach((item) => {
                    item.classList.toggle("selected", item === button);
                });
                elements.feedbackStatus.textContent = "Feedback saved locally for your evaluation summary.";
            });
        });
    }

    function init() {
        createWaveform();
        setupUploadEvents();
        setupPwa();
        setupShowcase();
        try {
            elements.consentCheckbox.checked = sessionStorage.getItem("voiceguard-consent") === "accepted";
        } catch {}
        elements.consentCheckbox.addEventListener("change", () => {
            try {
                if (elements.consentCheckbox.checked) sessionStorage.setItem("voiceguard-consent", "accepted");
                else sessionStorage.removeItem("voiceguard-consent");
            } catch {}
        });
        renderHistory();
        elements.clearHistory.addEventListener("click", () => {
            try { localStorage.removeItem(HISTORY_KEY); } catch {}
            renderHistory();
        });
        elements.liveButton.addEventListener("click", toggleLive);
        if (!window.MediaRecorder) {
            elements.recordButton.disabled = true;
            elements.recordButtonText.textContent = "Recording not supported in this browser";
        }
        window.addEventListener("pagehide", () => {
            if (liveState.running || liveState.starting) stopLive();
            if (recordState.active || recordState.starting) discardRecording();
            if (uploadState.previewUrl) URL.revokeObjectURL(uploadState.previewUrl);
        });
        window.addEventListener("online", checkService);
        window.addEventListener("offline", () => {
            elements.serviceState.classList.add("offline");
            elements.serviceState.classList.remove("online");
            elements.serviceStateText.textContent = "Offline";
        });
        checkService();
        window.setInterval(checkService, 15000);
    }

    init();
})();
