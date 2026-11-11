"""Step 2: Retrieval Tool Execution Step."""

from typing import List, Tuple, Optional
from relay.retrieval.tool import RetrievalTool, RetrievedPassage
from relay.pipeline.router import RouteDecision
from relay.pipeline.state import RequestContext


class RetrieverStep:
    """Step 2 orchestrator for passage retrieval tool call."""

    def __init__(self, tool: RetrievalTool):
        self.tool = tool

    def execute(
        self,
        ctx: RequestContext,
        route: RouteDecision,
        top_k: Optional[int] = None,
        query_override: str = "",
    ) -> Tuple[List[RetrievedPassage], str]:
        """Execute passage retrieval tool call.

        Returns:
            Tuple of (passages_list, formatted_context_string)
        """
        search_query = query_override or route.search_query or ctx.query
        passages = self.tool.retrieve_passages(
            query=search_query,
            top_k=top_k,
            paper_filter=route.paper_filter,
            quota_pressure=ctx.quota_pressure,
        )
        context_str = self.tool.format_context_for_prompt(passages)
        return passages, context_str
