local glyph = node.id("glyph")
local source_unicode = luatexbase.attributes.luatexkounicodeattr

-- Generated glyphs can be inserted after luaotfload has already selected fallback fonts.
local function set_glyph(n, id, char)
  local face = font.getfont(id)
  n.char = char
  n.font = face.characters[char] and id or assert(face.fallback_lookup[char], "missing label character")
end

local function label_missing(head, g)
  local label = string.format("[U+%04X]", g.char)
  local original_font = g.font
  set_glyph(g, original_font, label:byte(1))
  local previous = g
  for i = 2, #label do
    local part = node.copy(g)
    set_glyph(part, original_font, label:byte(i))
    head, previous = node.insert_after(head, previous, part)
  end
  return head
end

-- Shaping has already resolved combining sequences, invisible characters and fallback faces.
local function mark_missing(head)
  for g in node.traverse_id(glyph, head) do
    local face = font.getfont(g.font)
    if face and face.fallback_lookup and not face.characters[g.char] then
      head = label_missing(head, g)
    end
  end
  return head
end

-- The node shaper also uses private Unicode slots for internal glyphs. Resolve
-- literal input in that range against the font file's actual Unicode mapping.
local cmaps = {}
local function has_nominal(face, char)
  local path = face.filename
  if not cmaps[path] then
    local hb = luaotfload.harfbuzz
    cmaps[path] = hb.Font.new(hb.Face.new(path))
  end
  return cmaps[path]:get_nominal_glyph(char) ~= nil
end

local function check_private_slots(head, count)
  for g in node.traverse_id(glyph, head) do
    if g.char >= fonts.constructors.privateoffset and node.has_attribute(g, source_unicode) == g.char then
      local face = font.getfont(g.font)
      local lookup = face and face.fallback_lookup
      if lookup and not has_nominal(face, g.char) then
        local substitute
        for i = 1, count do
          local other = lookup[-i]
          if has_nominal(other, g.char) then
            substitute = fonts.definers.registered(other.properties.hash)
            break
          end
        end
        if substitute then g.font = substitute else head = label_missing(head, g) end
      end
    end
  end
  return head
end

return function(policy, font_basename)
  for shape, weight in pairs({regular="regular", bold="bold", italic="regular", bolditalic="bold"}) do
    local fallbacks = {}
    local slant = shape:find("italic", 1, true) and ";slant=0.2" or ""
    for _, family in ipairs(policy.font_order) do
      local file = font_basename(policy.fonts[family][weight])
      fallbacks[#fallbacks+1] = "[fonts/" .. file .. "]:mode=node;-tlig" .. slant
    end
    luaotfload.add_fallback("book" .. shape, fallbacks)
  end
  luatexbase.add_to_callback("pre_shaping_filter", function(head)
    return check_private_slots(head, #policy.font_order)
  end, "jjokgyeol.private_glyph")
  luatexbase.add_to_callback("post_shaping_filter", mark_missing, "jjokgyeol.missing_glyph")
  return set_glyph
end
