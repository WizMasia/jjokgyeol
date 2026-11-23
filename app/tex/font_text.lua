local glyph, disc = node.id("glyph"), node.id("disc")
local hlist, vlist = node.id("hlist"), node.id("vlist")
local aliases = {}

-- A single PDF glyph ID can only have one ToUnicode entry. All of its
-- source codepoints need ActualText, including the font's first mapping.
local function font_aliases(id)
  if not aliases[id] then
    local group = {}
    local resources = font.getfont(id).resources
    for first, others in pairs(resources and resources.duplicates or {}) do
      group[first] = true
      for char in pairs(others) do group[char] = true end
    end
    aliases[id] = group
  end
  return aliases[id]
end

local function literal(data)
  local n = node.new("whatsit", "pdf_literal")
  n.mode, n.data = 1, data -- page mode
  return n
end

local function mark_aliases(head)
  for n in node.traverse(head) do
    if n.id == glyph and font_aliases(n.font)[n.char] then
      local text = string.tohex(utf.utf8_to_utf16_be(utf8.char(n.char)))
      head = node.insert_before(head, n, literal("/Span<</ActualText<" .. text .. ">>>BDC"))
      node.insert_after(head, n, literal("EMC"))
    elseif n.id == hlist or n.id == vlist then
      n.head = mark_aliases(n.head)
    elseif n.id == disc and n.replace then
      n.replace = mark_aliases(n.replace)
    end
  end
  return head
end

return mark_aliases
