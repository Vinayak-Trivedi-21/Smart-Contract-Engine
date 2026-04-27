(function () {
    const API_BASE_URL = window.CONTRACT_API_BASE_URL || "http://localhost:8001";
    const CHATBOT_ENDPOINT = API_BASE_URL.replace(/\/$/, "") + "/chatbot/query";

    const toggle = document.getElementById("chatbotToggle");
    const panel = document.getElementById("chatbotPanel");
    const closeButton = document.getElementById("chatbotClose");
    const form = document.getElementById("chatbotForm");
    const input = document.getElementById("chatbotInput");
    const messages = document.getElementById("chatbotMessages");

    if (!toggle || !panel || !closeButton || !form || !input || !messages) {
        return;
    }

    function setOpen(isOpen) {
        panel.hidden = !isOpen;
        toggle.setAttribute("aria-expanded", String(isOpen));
        toggle.setAttribute("aria-label", isOpen ? "Close chatbot" : "Open chatbot");
        if (isOpen) {
            input.focus();
        }
    }

    function appendMessage(text, role) {
        const bubble = document.createElement("p");
        bubble.className = "chatbot-message " + role;
        bubble.textContent = text;
        messages.appendChild(bubble);
        messages.scrollTop = messages.scrollHeight;
        return bubble;
    }

    function appendReferences(referenceItems) {
        if (!Array.isArray(referenceItems) || referenceItems.length === 0) {
            return;
        }

        const references = document.createElement("p");
        references.className = "chatbot-message bot";
        references.textContent = "Sources: " + referenceItems.join(" | ");
        messages.appendChild(references);
        messages.scrollTop = messages.scrollHeight;
    }

    toggle.addEventListener("click", function () {
        setOpen(panel.hidden);
    });

    closeButton.addEventListener("click", function () {
        setOpen(false);
        toggle.focus();
    });

    form.addEventListener("submit", async function (event) {
        event.preventDefault();
        const question = input.value.trim();
        if (!question) {
            return;
        }

        appendMessage(question, "user");
        input.value = "";

        const pending = appendMessage("Thinking...", "bot");
        input.disabled = true;

        try {
            const response = await fetch(CHATBOT_ENDPOINT, {
                method: "POST",
                headers: {
                    "Content-Type": "application/json"
                },
                body: JSON.stringify({ question: question })
            });

            if (!response.ok) {
                let message = "Chat request failed.";
                try {
                    const data = await response.json();
                    if (data && data.detail) {
                        message = data.detail;
                    }
                } catch (_) {
                    // Keep default message when response body is not JSON.
                }
                throw new Error(message);
            }

            const data = await response.json();
            pending.remove();
            appendMessage((data && data.answer) || "No response from assistant.", "bot");
            appendReferences(data && data.references);
        } catch (error) {
            pending.remove();
            appendMessage(error.message || "Unable to reach chatbot backend.", "bot");
        } finally {
            input.disabled = false;
            input.focus();
        }
    });

    document.addEventListener("keydown", function (event) {
        if (event.key === "Escape" && !panel.hidden) {
            setOpen(false);
            toggle.focus();
        }
    });
})();
