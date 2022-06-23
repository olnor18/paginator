from asyncio import TimeoutError
from enum import Enum
from inspect import iscoroutinefunction
from itertools import chain
from random import randint
from typing import Any, Awaitable, Callable, Coroutine, Dict, List, Optional, Union

from interactions.ext.wait_for import setup, wait_for_component

from interactions import (
    MISSING,
    ActionRow,
    Button,
    Client,
    CommandContext,
    ComponentContext,
    Embed,
    Emoji,
    Message,
    SelectMenu,
    SelectOption,
)
from interactions.ext import Converter as _Converter

from .errors import PaginatorWontWork, StopPaginator


class Converter(_Converter):
    @property
    def difference(self) -> List[dict]:
        return [{key: val} for key, val in self._obj2.items() if key not in self._obj1]


class ButtonKind(str, Enum):
    """
    Enum for button types.

    Enums:

    - `FIRST: "first"`
    - `PREVIOUS: "prev"`
    - `INDEX: "index"`
    - `NEXT: "next"`
    - `LAST: "last"`
    """

    FIRST = "first"
    PREVIOUS = "prev"
    INDEX = "index"
    NEXT = "next"
    LAST = "last"


class RowPosition(str, Enum):
    """
    Enum for position of components.

    Enums:

    - `TOP: "top"`
    - `MIDDLE: "mid"`
    - `BOTTOM: "low"`
    """

    TOP = "top"
    MIDDLE = "mid"
    BOTTOM = "low"


class DictSerializerMixin:
    __slots__ = ("_json",)

    def __init__(self, **kwargs):
        self._json = kwargs

        for key, value in kwargs.items():
            if not hasattr(self, "__slots__") or key in self.__slots__:
                setattr(self, key, value)

        if hasattr(self, "__slots__"):
            for _attr in self.__slots__:
                if not hasattr(self, _attr):
                    setattr(self, _attr, None)


class Data(DictSerializerMixin):
    """
    Data that is returned once the paginator times out.

    Attrinutes:

    - `paginator: Paginator`: The paginator that timed out.
    - `original_ctx: CommandContext | ComponentContext`: The original context.
    - `component_ctx: ComponentContext`: The context of the component.
    - `message: Message`: The message that was sent.
    """

    __slots__ = ("_json", "paginator", "original_ctx", "component_ctx", "message")
    _json: Dict[str, Any]
    paginator: "Paginator"
    original_ctx: Union[CommandContext, ComponentContext]
    component_ctx: ComponentContext
    message: Message

    def __repr__(self) -> str:
        return f"<Data paginator={self.paginator}, original_ctx={self.original_ctx}, component_ctx={self.component_ctx}, message={self.message}>"

    __str__ = __repr__


class Page(DictSerializerMixin):
    """
    An individual page to be supplied as a list of these pages to the paginator.

    Parameters:

    - `?content: str`: The content of the page.
    - `?embeds: Embed | list[Embed]`: The embeds of the page.
    - `?title: str`: The title of the page displayed in the select menu.
        - Defaults to content or the title of the embed with an available title.
    - `?components: ActionRow`: The custom components of the page.
    - `?callback: Callable[[Paginator, ComponentContext], Awaitable]`: The callback to run when any of the components are clicked.
    - `?position: RowPosition`: The position of the components.
    """

    __slots__ = ("_json", "content", "embeds", "title", "components", "callback", "position")
    _json: Dict[str, Any]
    content: Optional[str]
    embeds: Optional[Union[Embed, List[Embed]]]
    title: Optional[str]
    components: Optional[ActionRow]
    callback: Optional[Callable[["Paginator", ComponentContext], Awaitable]]
    position: RowPosition

    def __init__(
        self,
        content: Optional[str] = None,
        embeds: Optional[Union[Embed, List[Embed]]] = None,
        title: Optional[str] = None,
        components: Optional[ActionRow] = None,
        callback: Optional[Callable[["Paginator", ComponentContext], Awaitable]] = None,
        position: RowPosition = RowPosition.TOP,
    ) -> None:
        if title:
            _title = title
        elif content:
            _title = f"{content[:93]}..." if len(content) > 96 else content
        elif embeds and isinstance(embeds, Embed) and embeds.title:
            _title = f"{embeds.title[:93]}..." if len(embeds.title) > 96 else embeds.title
        elif embeds and isinstance(embeds, list) and embeds[0].title:
            _title = next(
                (
                    f"{embed.title[:93]}..." if len(embed.title) > 96 else embed.title
                    for embed in embeds
                    if embed.title
                ),
                "No title",
            )
        else:
            _title = "No title"

        super().__init__(
            content=content,
            embeds=embeds,
            title=_title,
            components=components,
            position=position,
            callback=callback,
        )

    @property
    def data(self) -> Dict[str, Any]:
        return {"content": self.content, "embeds": self.embeds}

    async def run_callback(self, paginator: "Paginator", ctx: ComponentContext) -> None:
        if not self.callback:
            return
        if not self.components:
            return

        custom_id = ctx.data.custom_id
        if all(custom_id != c.custom_id for c in self.components.components):
            return

        return await self.callback(paginator, ctx)

    def __repr__(self) -> str:
        return f"<Page title={self.title}>"

    __str__ = __repr__


class Paginator(DictSerializerMixin):
    """
    The paginator.

    Parameters:

    - `client: Client`: The client.
    - `ctx: CommandContext | ComponentContext`: The context.
    - `pages: list[Page]`: The pages to paginate.
    - `?timeout: int | float | None`: The timeout in seconds. Defaults to 60.
    - `?author_only: bool`: Whether to only allow the author to edit the message. Defaults to False.
    - `?use_buttons: bool`: Whether to use buttons. Defaults to True.
    - `?use_select: bool`: Whether to use the select menu. Defaults to True.
    - `?use_index: bool`: Whether the paginator should use the index button. Defaults to False.
    - `?extended_buttons: bool`: Whether to use extended buttons. Defaults to True.
    - `?buttons: dict[str, Button]`: The customized buttons to use. Defaults to None. Use `ButtonKind` as the key.
    - `?custom_buttons: Button | list[Button]`: The customized buttons to add. Defaults to None. Will not be added if it does not fit.
    - `?custom_callback: Callable[[Paginator, ComponentContext], Awaitable]`: The callback to run when any of the custom components are clicked.
    - `?placeholder: str`: The placeholder to use for the select menu. Defaults to "Page".
    - `?disable_after_timeout: bool`: Whether to disable the components after timeout. Defaults to True.
    - `?remove_after_timeout: bool`: Whether to remove the components after timeout. Defaults to False.
    - `?func_before_edit: Callable`: The function to run before editing the message.
    - `?func_after_edit: Callable`: The function to run after editing the message.

    Attributes:

    - `id: int`: The ID of the paginator.
    - `index: int`: The index of the current page.
    - `prev_index: int`: The index of the previous page.
    - `top: int`: index of the top page.
    - `?component_ctx: ComponentContext`: The context of the component.
    - `?message: Message`: The message that is being paginated.

    Methods:

    - *async* `run() -> ?Data`: Runs the paginator in a loop until timed out.
    - *property* `custom_ids -> list[str]`: The custom IDs of the components.
    - *async* `component_logic()`: The logic for the components when clicked.
    - *async* `check(ctx: ComponentContext) -> bool`: Whether the paginator is for the user.
    - *func* `page_row() -> ?ActionRow`: The custom components of the page.
    - *func* `select_row() -> ?ActionRow`: The select action row.
    - *func* `buttons_row() -> ?ActionRow`: The buttons action row.
    - *func* `components() -> list[?ActionRow]`: The components as action rows.
    - *async* `send() -> Message`: Sends the paginator.
    - *async* `edit() -> Message`: Edits the paginator.
    - *func* `disabled_components() -> list[?ActionRow]`: The disabled components as action rows.
    - *func* `removed_components()`: The removed components.
    - *async* `end_paginator()`: Ends the paginator.
    - *async* `run_function(func: Callable) -> bool`: Runs a function.
    - *func* `data() -> Data`: The data of the paginator.
    """

    __slots__ = (
        "_json",
        "client",
        "ctx",
        "pages",
        "timeout",
        "author_only",
        "use_buttons",
        "use_select",
        "use_index",
        "extended_buttons",
        "buttons",
        "custom_buttons",
        "custom_callback",
        "placeholder",
        "disable_after_timeout",
        "remove_after_timeout",
        "func_before_edit",
        "func_after_edit",
        "id",
        "component_ctx",
        "index",
        "prev_index",
        "top",
        "is_dict",
        "is_embeds",
        "message",
    )
    _json: Dict[str, Any]
    client: Client
    ctx: Union[CommandContext, ComponentContext]
    pages: List[Page]
    timeout: Optional[Union[int, float]]
    author_only: bool
    use_buttons: bool
    use_select: bool
    use_index: bool
    extended_buttons: bool
    buttons: Optional[Dict[str, Button]]
    custom_buttons: Optional[Union[Button, List[Button]]]
    custom_callback: Optional[Coroutine]
    placeholder: str
    disable_after_timeout: bool
    remove_after_timeout: bool
    func_before_edit: Optional[Union[Callable, Coroutine]]
    func_after_edit: Optional[Union[Callable, Coroutine]]
    id: int
    component_ctx: Optional[ComponentContext]
    index: int
    prev_index: int
    top: int
    is_dict: bool
    is_embeds: bool
    message: Message

    def __init__(
        self,
        client: Client,
        ctx: Union[CommandContext, ComponentContext],
        pages: List[Page],
        timeout: Optional[Union[int, float]] = 60,
        author_only: bool = False,
        use_buttons: bool = True,
        use_select: bool = True,
        use_index: bool = False,
        extended_buttons: bool = True,
        buttons: Optional[Dict[str, Button]] = None,
        custom_buttons: Optional[Union[Button, List[Button]]] = None,
        custom_callback: Optional[Coroutine] = None,
        placeholder: str = "Page",
        disable_after_timeout: bool = True,
        remove_after_timeout: bool = False,
        func_before_edit: Optional[Union[Callable, Coroutine]] = None,
        func_after_edit: Optional[Union[Callable, Coroutine]] = None,
        **kwargs,
    ) -> None:
        if not (use_buttons or use_select):
            raise PaginatorWontWork(
                "You need either buttons, select, or both, or else the paginator wont work!"
            )
        if len(pages) < 2:
            raise PaginatorWontWork("You need more than one page!")
        if not all(isinstance(page, Page) for page in pages):
            raise PaginatorWontWork("All pages must be of type `Page`!")
        if not hasattr(client, "wait_for_component"):
            setup(client)

        super().__init__(
            client=client,
            ctx=ctx,
            pages=pages,
            timeout=timeout,
            author_only=author_only,
            use_buttons=use_buttons,
            use_select=use_select,
            use_index=use_index,
            extended_buttons=extended_buttons,
            buttons={} if buttons is None else buttons,
            custom_buttons=[custom_buttons]
            if isinstance(custom_buttons, Button)
            else custom_buttons,
            custom_callback=custom_callback,
            placeholder=placeholder,
            disable_after_timeout=disable_after_timeout,
            remove_after_timeout=remove_after_timeout,
            func_before_edit=func_before_edit,
            func_after_edit=func_after_edit,
            **kwargs,
        )
        self.id: int = kwargs.get("id", randint(0, 999_999_999))
        self.component_ctx: Optional[ComponentContext] = kwargs.get("component_ctx")
        self.index: int = kwargs.get("index", 0)
        self.prev_index: int = kwargs.get("prev_index", 0)
        self.top: int = kwargs.get("top", len(pages) - 1)
        self.message: Optional[Message] = kwargs.get("message")

    async def run(self) -> Data:
        self.message = await self.send()
        while True:
            try:
                self.component_ctx: ComponentContext = await wait_for_component(
                    self.client,
                    None,
                    self.message.id,
                    self.check,
                    self.timeout,
                )
            except TimeoutError:
                await self.end_paginator()
                return self.data()
            result: Optional[bool] = None
            if self.func_before_edit is not None:
                try:
                    result: Optional[bool] = await self.run_function(self.func_before_edit)
                except StopPaginator:
                    return self.data()
                if result is False:
                    continue
            await self.component_logic()
            self.message = await self.edit()
            self.prev_index = self.index
            if self.func_after_edit is not None:
                try:
                    result: Optional[bool] = await self.run_function(self.func_after_edit)
                except StopPaginator:
                    return self.data()
                if result is False:
                    continue

    @property
    def custom_ids(self) -> List[str]:
        return [
            f"select{self.id}",
            f"first{self.id}",
            f"prev{self.id}",
            f"index{self.id}",
            f"next{self.id}",
            f"last{self.id}",
        ]

    async def component_logic(self) -> None:
        custom_id: str = self.component_ctx.data.custom_id
        if custom_id == f"select{self.id}":
            self.index = int(self.component_ctx.data.values[0]) - 1
        elif custom_id == f"first{self.id}":
            self.index = 0
        elif custom_id == f"prev{self.id}":
            self.index -= 1
            self.index = max(self.index, 0)
        elif custom_id == f"next{self.id}":
            self.index += 1
            self.index = min(self.index, self.top)
        elif custom_id == f"last{self.id}":
            self.index = self.top
        else:
            res: Optional[int] = await self.pages[self.index].run_callback(self, self.component_ctx)
            if res in range(len(self.pages)):
                self.index = res

            if self.custom_callback and not res:
                try:
                    res: Optional[int] = await self.run_function(self.custom_callback)
                except StopPaginator:
                    return self.data()
                if res in range(len(self.pages)):
                    self.index = res

    async def check(self, ctx: ComponentContext) -> bool:
        boolean: bool = True
        if self.author_only:
            boolean = ctx.user.id == self.ctx.user.id
        if not boolean:
            await ctx.send("This paginator is not for you!", ephemeral=True)
        return boolean

    def page_row(self) -> Optional[ActionRow]:
        return getattr(self.pages[self.index], "components", None)

    def select_row(self) -> Optional[ActionRow]:
        if not self.use_select or len(self.pages) > 25:
            return

        select_options = [
            SelectOption(label=f"{page_num}: {page.title}", value=page_num)
            for page_num, page in enumerate(self.pages, start=1)
        ]

        select = SelectMenu(
            options=select_options,
            custom_id=f"select{self.id}",
            placeholder=f"{self.placeholder} {self.index + 1}/{self.top + 1}",
            min_values=1,
            max_values=1,
        )
        return ActionRow(components=[select])

    def buttons_row(self) -> Optional[ActionRow]:
        if not self.use_buttons:
            return

        disabled_left = self.index == 0
        disabled_right = self.index == self.top
        buttons = [
            self.buttons.get("first", Button(style=1, emoji=Emoji(name="⏮️")))
            if self.extended_buttons
            else None,
            self.buttons.get("prev", Button(style=1, emoji=Emoji(name="◀️"))),
            self.buttons.get(
                "index",
                Button(style=1, label=f"{self.placeholder} {self.index + 1}/{self.top + 1}"),
            )
            if self.use_index
            else None,
            self.buttons.get("next", Button(style=1, emoji=Emoji(name="▶️"))),
            self.buttons.get("last", Button(style=1, emoji=Emoji(name="⏭️")))
            if self.extended_buttons
            else None,
        ]

        for i, button in enumerate(buttons):
            if button is None:
                continue
            button.custom_id = self.custom_ids[i + 1]
            button._json.update({"custom_id": button.custom_id})
            button.disabled = (
                disabled_left
                if button.custom_id in self.custom_ids[1:3]
                else True
                if button.custom_id == self.custom_ids[3]
                else disabled_right
            )
            button._json.update({"disabled": button.disabled})
            if button.custom_id == self.custom_ids[3]:
                button.label = f"{self.placeholder} {self.index + 1}/{self.top + 1}"
                button._json.update({"label": button.label})

        buttons = list(filter(None, buttons))
        if self.custom_buttons and len(self.custom_buttons) + len(buttons) <= 5:
            buttons.extend(self.custom_buttons)

        return ActionRow(components=list(filter(None, buttons)))

    def components(self) -> List[ActionRow]:
        rows = [self.select_row(), self.buttons_row()]
        if row := self.page_row():
            pos: RowPosition = self.pages[self.index].position
            if pos == RowPosition.TOP:
                rows.insert(0, row)
            elif pos == RowPosition.MIDDLE:
                rows.insert(1, row)
            elif pos == RowPosition.BOTTOM:
                rows.append(row)
        return list(filter(None, rows))

    async def send(self) -> Message:
        return await self.ctx.send(components=self.components(), **self.pages[self.index].data)

    async def edit(self) -> Message:
        self.component_change()
        if self.component_ctx.responded and (
            self.prev_index != self.index or self.component_change()
        ):
            return await self.message.edit(
                components=self.components(), **self.pages[self.index].data
            )
        msg = await self.component_ctx.edit(
            components=self.components(), **self.pages[self.index].data
        )
        for attr in msg.__slots__:
            if getattr(self.message, attr, None) and not getattr(msg, attr, None):
                setattr(msg, attr, getattr(self.message, attr))
                msg._json[attr] = self.message._json[attr]
        return msg

    def disabled_components(self) -> List[ActionRow]:
        components = self.components()
        for action_row in components:
            for component in action_row.components:
                component.disabled = True
        return components

    def removed_components(self) -> None:
        return

    async def end_paginator(self) -> None:
        await self.message.edit(
            components=self.removed_components()
            if self.remove_after_timeout
            else self.disabled_components()
            if self.disable_after_timeout
            else MISSING
        )

    async def run_function(self, func) -> bool:
        if func is not None:
            if iscoroutinefunction(func):
                return await func(self, self.component_ctx)
            else:
                return func(self, self.component_ctx)

    def component_change(self) -> bool:
        if not self.message:
            return False
        
        current = self.message.components
        new = []
        for row in self.components():
            new.extend(c._json for c in row.components)
        current = list(chain.from_iterable(a["components"] for a in current))

        for c1, c2 in zip(current, new):
            converted = Converter(c1, c2)
            if converted.difference:
                return True
        return False

    def data(self) -> Data:
        return Data(
            paginator=self,
            original_ctx=self.ctx,
            component_ctx=self.component_ctx,
            message=self.message,
        )

    def __repr__(self) -> str:
        return f"<Paginator id={self.id}>"

    __str__ = __repr__

    def __setattr__(self, __name: str, __value: Any) -> None:
        if __name == "_json":
            object.__setattr__(self, "_json", __value)
        elif __name in self.__slots__:
            self._json.update({__name: __value})
        return super().__setattr__(__name, __value)
