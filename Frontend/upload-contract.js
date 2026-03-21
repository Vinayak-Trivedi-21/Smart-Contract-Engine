(function () {
    const API_BASE_URL = window.CONTRACT_API_BASE_URL || "http://localhost:8001";
    const API_ENDPOINT = API_BASE_URL.replace(/\/$/, "") + "/contractUpload";

    const form = document.getElementById("uploadContractForm");
    const fileInput = document.getElementById("contractFile");
    const selectedFile = document.getElementById("selectedFile");
    const clearSelectedFile = document.getElementById("clearSelectedFile");
    const uploadMessage = document.getElementById("uploadMessage");

    if (!form || !fileInput || !selectedFile || !clearSelectedFile || !uploadMessage) {
        return;
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
        uploadMessage.textContent = "File selection cleared.";
        updateSelectedFileUI();
    });

    form.addEventListener("submit", async function (event) {
        event.preventDefault();

        const file = fileInput.files && fileInput.files[0];
        if (!file) {
            uploadMessage.textContent = "Choose a file before uploading.";
            return;
        }

        const formData = new FormData();
        formData.append("contractFile", file);

        uploadMessage.textContent = "Uploading contract...";

        try {
            const response = await fetch(API_ENDPOINT, {
                method: "POST",
                body: formData,
            });

            let payload = null;
            try {
                payload = await response.json();
            } catch (error) {
                payload = null;
            }

            if (!response.ok) {
                const serverMessage = payload && (payload.detail || payload.message);
                uploadMessage.textContent = serverMessage || "Upload failed. Please try again.";
                return;
            }

            const successMessage = (payload && payload.message) || "Contract uploaded successfully.";
            const objectPath = payload && payload.objectPath;
            uploadMessage.textContent = objectPath ? successMessage + " Stored at " + objectPath : successMessage;
            form.reset();
            updateSelectedFileUI();
        } catch (error) {
            uploadMessage.textContent = "Could not connect to backend at /contractUpload.";
        }
    });

    updateSelectedFileUI();
})();
