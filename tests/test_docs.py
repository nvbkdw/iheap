"""Execute every ```python block in docs/*.md so the guide cannot go stale.

Blocks of one page run in order in a shared namespace, like a reader typing
them into one interpreter session.
"""

import pathlib
import re

import pytest

DOCS = pathlib.Path(__file__).parent.parent / "docs"
BLOCK = re.compile(r"^```python\n(.*?)^```", re.MULTILINE | re.DOTALL)


@pytest.mark.parametrize("page", sorted(DOCS.glob("*.md")), ids=lambda p: p.name)
def test_code_blocks_run(page):
    namespace: dict = {}
    for i, block in enumerate(BLOCK.findall(page.read_text())):
        exec(compile(block, f"{page.name}[block {i}]", "exec"), namespace)
