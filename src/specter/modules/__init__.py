from __future__ import annotations

from ..config import Config
from .base import BaseModule
from .crossref import CrossrefModule
from .github_user import GithubUserModule
from .gravatar import GravatarModule
from .hibp_breach import HibpBreachModule
from .news_gdelt import NewsGdeltModule
from .npm_user import NpmUserModule
from .openalex import OpenAlexModule
from .orcid import OrcidModule
from .pgp_keys import PgpKeysModule
from .pivot_crawler import PivotCrawlerModule
from .rdap_domain import RdapDomainModule
from .search_ddg import SearchDdgModule
from .sec_edgar import SecEdgarModule
from .sherlock import SherlockModule
from .stack_exchange import StackExchangeModule
from .wayback import WaybackModule
from .wikidata_tree import WikidataTreeModule


def all_modules(cfg: Config) -> list[BaseModule]:
    return [
        PivotCrawlerModule(),
        PgpKeysModule(),
        RdapDomainModule(),
        SearchDdgModule(),
        NewsGdeltModule(),
        WaybackModule(),
        SherlockModule(),
        GithubUserModule(),
        NpmUserModule(),
        GravatarModule(),
        OrcidModule(),
        CrossrefModule(),
        OpenAlexModule(),
        SecEdgarModule(),
        StackExchangeModule(),
        WikidataTreeModule(),
        HibpBreachModule(),
    ]
