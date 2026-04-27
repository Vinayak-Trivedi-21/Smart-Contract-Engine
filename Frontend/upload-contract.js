(function () {
    const WEBHOOK_ENDPOINT =
        window.CONTRACT_INGEST_WEBHOOK_URL || "http://localhost:5678/webhook/ingest-contract";

    const form = document.getElementById("uploadContractForm");
    const fileInput = document.getElementById("contractFile");
    const userIdInput = document.getElementById("userId");
    const selectedFile = document.getElementById("selectedFile");
    const clearSelectedFile = document.getElementById("clearSelectedFile");
    const uploadMessage = document.getElementById("uploadMessage");
    const submitButton = form && form.querySelector('button[type="submit"]');

    if (!form || !fileInput || !userIdInput || !selectedFile || !clearSelectedFile || !uploadMessage || !submitButton) {
        return;
    }

    function setUploadMessage(message, isError) {
        uploadMessage.textContent = message;
        uploadMessage.style.color = isError ? "#b42318" : "#156d2b";
    }

    function toBase64(file) {
        return new Promise(function (resolve, reject) {
            const reader = new FileReader();
            reader.readAsDataURL(file);
            reader.onload = function () {
                resolve(reader.result || "");
            };
            reader.onerror = reject;
        });
    }

    function updateSelectedFileUI() {
        const file = fileInput.files && fileInput.files[0];
        selectedFile.textContent = file ? "Selected file: " + file.name : "No file selected.";
        clearSelectedFile.hidden = !file;
    }

    fileInput.addEventListener("change", function () {
        updateSelectedFileUI();
    });

    clearSelectedFile.addEventListener("click", function () {
        fileInput.value = "";
        setUploadMessage("File selection cleared.", false);
        updateSelectedFileUI();
    });

    form.addEventListener("submit", async function (event) {
        event.preventDefault();

        const file = fileInput.files && fileInput.files[0];
        const userId = userIdInput.value.trim();

        if (!userId) {
            setUploadMessage("Enter a user ID before uploading.", true);
            return;
        }

        if (!file) {
            setUploadMessage("Choose a file before uploading.", true);
            return;
        }

        setUploadMessage("Uploading contract...", false);
        submitButton.disabled = true;

        try {
            const fileAsBase64 = await toBase64(file);
            const payload = {
                user_id: userId,
                filename: file.name,
                file: String(fileAsBase64).split(",")[1] || "",
            };

            const response = await fetch(WEBHOOK_ENDPOINT, {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                },
                body: JSON.stringify(payload),
            });

            const rawText = await response.text();
            let responseData = null;
            try {
                responseData = rawText ? JSON.parse(rawText) : null;
            } catch (error) {
                responseData = null;
            }

            if (!response.ok) {
                const serverMessage = responseData && (responseData.detail || responseData.message);
                setUploadMessage(serverMessage || "Upload failed. Please try again.", true);
                return;
            }

            if (responseData && responseData.status === "uploaded") {
                setUploadMessage("Contract uploaded successfully.", false);
            } else if (responseData && responseData.status) {
                setUploadMessage("Upload completed with status: " + responseData.status, false);
            } else {
                setUploadMessage("Upload completed.", false);
            }

            form.reset();
            updateSelectedFileUI();
        } catch (error) {
            setUploadMessage("Could not reach the upload webhook. Check if n8n is running.", true);
        } finally {
            submitButton.disabled = false;
        }
    });

    updateSelectedFileUI();
})();
