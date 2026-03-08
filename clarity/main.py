from pathlib import Path
import random
from textwrap import dedent
import openai
from anthropic import Anthropic
from pydantic import BaseModel
import streamlit as st
import yaml

import constants  # Needs to be imported first, as it loads the environment variables.
from formatting import mk_diff, fmt_diff_toggles
from llm import ai_stream


class Config(BaseModel):
    api_base: str | None = None
    api_key: str | None = None
    provider: str = "anthropic"  # "openai" or "anthropic"

    @classmethod
    def load(cls) -> "Config":
        try:
            path = Path(constants.CONFIG_PATH)
            data = yaml.safe_load(path.read_text())
            return cls.model_validate(data)
        except FileNotFoundError:
            return cls()


def main():
    st.set_page_config(initial_sidebar_state="collapsed", page_title="Clarity")

    config = Config.load()
    if config.provider == "anthropic":
        client = Anthropic(
            api_key=config.api_key,
            base_url=config.api_base,
        )
    else:
        client = openai.OpenAI(
            api_key=config.api_key,
            base_url=config.api_base,
        )

    st.title("Clarity", anchor=False)

    all_hearts = "❤️-🧡-💛-💚-💙-💜-🖤-🤍-🤎-💖-❤️‍🔥".split("-")
    heart = random.choice(all_hearts)

    st.sidebar.write(
        f"""
        # How to use this tool?
        It's simple.
        1. Paste in some text
        2. Get an AI to improve it
        3. Review the suggestions:
            :red[red text is yours], :green[green is suggestions].
        4. Click to toggle diffs between the original and new version.

        Made with {heart} by [AJ Weeks](https://ajweeks.com), forked from [Diego Dorn](https://ddorn.fr)'s [typofixer](https://github.com/ddorn/typofixer).
        """
    )

    st.sidebar.write(
        """
        ## Privacy
        Your data is sent to my server, where it is not stored and is forwarded to
        Groq/OpenAI/Anthropic depending on your choice of model. I only log the size of the requests to monitor usage.
        You can also run this locally by following the instructions on the [GitHub repo](
        https://github.com/ajweeks/clarity). Groq claims to not store/train on/sell your data, and OpenAI/Anthropic
        do the same, but might keep it for 30 days, unless it is classified as violating their TOS, in which case
        they keep if for up to 2 years.
        """
    )

    system_prompts = {
        "Fix typos": """
            You are a meticulous copy editor. Correct spelling, grammar, punctuation, and basic phrasing mistakes.
            Preserve the original meaning and tone. Keep sentence structure unless a change is needed for correctness.
            Fix minor formatting issues (spacing, quotes, bullets). Use inclusive language when applicable.
            Output only the corrected text with no commentary.
            """,
        "Heavy fix": """
            You are an expert editor and stylist. Improve clarity, flow, and idiomatic phrasing while preserving meaning.
            Replace vague or repetitive words with more precise, natural alternatives. Vary sentence structure for readability.
            Fix grammar, punctuation, and formatting issues; ensure a professional, inclusive tone.
            Output only the revised text with no commentary.
            """,
        "Custom": "",
    }

    if "system_name" not in st.session_state:
        st.session_state.system_name = "Fix typos"
    if "prompt_text" not in st.session_state:
        st.session_state.prompt_text = dedent(
            system_prompts[st.session_state.system_name]
        ).strip()
    if "pending_system_name" not in st.session_state:
        st.session_state.pending_system_name = None

    def on_prompt_select() -> None:
        if st.session_state.system_name == "Custom":
            return
        st.session_state.prompt_text = dedent(
            system_prompts[st.session_state.system_name]
        ).strip()

    def on_prompt_edit() -> None:
        st.session_state.pending_system_name = "Custom"

    if st.session_state.system_name != "Custom":
        selected_prompt = dedent(
            system_prompts[st.session_state.system_name]
        ).strip()
        if st.session_state.prompt_text != selected_prompt:
            st.session_state.pending_system_name = "Custom"

    if st.session_state.pending_system_name:
        st.session_state.system_name = st.session_state.pending_system_name
        st.session_state.pending_system_name = None

    system_name = st.radio(
        "Prompt",
        list(system_prompts.keys()),
        horizontal=True,
        key="system_name",
        on_change=on_prompt_select,
    )
    assert system_name is not None  # For type checker

    with st.expander("Prompt", expanded=False):
        st.text_area(
            "Prompt text",
            max_chars=constants.MAX_CHARS,
            key="prompt_text",
            on_change=on_prompt_edit,
        )

    with st.form(key="fix"):
        system = st.session_state.prompt_text

        text = st.text_area(
            "Text to fix",
            max_chars=constants.MAX_CHARS,
            height=220,
        )

        if config.provider == "openai":
            models = client.models.list()
            model_names = [model.id for model in models.data]
            # Sort: groq first, then alphabetically
            model_names.sort(key=lambda x: ("groq" not in x, x))
            model = st.selectbox(
                "Model",
                model_names,
            )
        else:
            model = "claude-sonnet-4-6"
        assert model is not None  # For the type checker.

        lets_gooo = st.form_submit_button("Fix", type="primary")

    @st.cache_resource()
    def cache():
        return {}

    if lets_gooo:
        corrected = st.write_stream(
            ai_stream(
                system,
                [dict(role="user", content=text)],
                model=model,
                client=client,
            )
        )
        cache()[text, system] = corrected
        st.rerun()
    else:
        corrected = cache().get((text, system))

    dev_mode = st.sidebar.toggle("Developer mode")
    if dev_mode:
        text = st.text_area("Text to fix", text, height=400)
        corrected = st.text_area("Corrected text", corrected, height=400)
        st.write(corrected)

    if corrected is not None:
        # Compute the difference between the two texts
        diff = mk_diff(text, corrected)

        st.header("Corrected text")
        options = [":red[Original text]", ":green[New suggestions]"]
        selected = st.radio("Select all", options, index=1, horizontal=True)

        with st.container(border=True):
            st.html(fmt_diff_toggles(diff, start_with_old_selected=selected == options[0]))

        st.warning(
            "This text was written by a generative AI model. You **ALWAYS** need to review it."
        )

        st.expander("LLM version of the text").text(corrected)
    else:
        diff = "No diff yet"

    if dev_mode:
        st.expander("Raw diff").write(diff)


if __name__ == "__main__":
    main()
