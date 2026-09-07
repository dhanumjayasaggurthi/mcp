import React, { useState, useMemo, useRef, useEffect } from "react";

export default function DataTable({ records, searchedKeyword }) {
  const [modal, setModal] = useState(null);
  const [columnWidths, setColumnWidths] = useState({});
  const [resizingColumn, setResizingColumn] = useState(null);
  const [expandedRows, setExpandedRows] = useState(new Set());
  const resizeRef = useRef({ startX: 0, startWidth: 0, column: null });

  // Memoized helper to check if text contains a match
  const hasKeywordMatch = useMemo(() => {
    return (text, keyword) => {
      if (!keyword || keyword === '' || text == null) return false;

      const textStr = String(text);
      const keywordStr = String(keyword).trim();

      if (!keywordStr || textStr === '') return false;

      try {
        const isFuzzySearch = keywordStr.includes('~');
        const cleanKeyword = keywordStr.split('~')[0].trim();

        if (!cleanKeyword) return false;

        const escapedKeyword = cleanKeyword.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
        const regex = isFuzzySearch
          ? new RegExp(escapedKeyword, 'i')
          : new RegExp(`\\b${escapedKeyword}\\b`, 'i');

        return regex.test(textStr);
      } catch (error) {
        console.error('Error in hasKeywordMatch:', error);
        return false;
      }
    };
  }, []);

  // Memoized helper to highlight matching text
  const highlightText = useMemo(() => {
    return (text, keyword) => {
      if (!keyword || keyword === '' || text == null) return text;

      const textStr = String(text);
      const keywordStr = String(keyword).trim();

      if (!keywordStr) return textStr;

      try {
        const isFuzzySearch = keywordStr.includes('~');
        const cleanKeyword = keywordStr.split('~')[0].trim();

        if (!cleanKeyword) return textStr;

        const escapedKeyword = cleanKeyword.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
        const searchRegex = isFuzzySearch
          ? new RegExp(escapedKeyword, 'gi')
          : new RegExp(`\\b${escapedKeyword}\\b`, 'gi');

        const matches = [];
        let match;

        searchRegex.lastIndex = 0;

        while ((match = searchRegex.exec(textStr)) !== null) {
          matches.push({
            start: match.index,
            end: match.index + match[0].length,
            text: match[0]
          });

          if (match.index === searchRegex.lastIndex) {
            searchRegex.lastIndex++;
          }
        }

        if (matches.length === 0) return textStr;

        const result = [];
        let lastIndex = 0;

        matches.forEach((matchInfo, idx) => {
          if (matchInfo.start > lastIndex) {
            result.push(
              <React.Fragment key={`before-${idx}-${lastIndex}`}>
                {textStr.substring(lastIndex, matchInfo.start)}
              </React.Fragment>
            );
          }

          result.push(
            <mark
              key={`match-${idx}-${matchInfo.start}`}
              className="bg-yellow-300 text-jj-gray-08 font-johnson-text px-0.5 rounded"
            >
              {matchInfo.text}
            </mark>
          );

          lastIndex = matchInfo.end;
        });

        if (lastIndex < textStr.length) {
          result.push(
            <React.Fragment key={`after-${lastIndex}`}>
              {textStr.substring(lastIndex)}
            </React.Fragment>
          );
        }

        return result.length > 0 ? result : textStr;
      } catch (error) {
        console.error('Error in highlightText:', error);
        return textStr;
      }
    };
  }, []);

  // Column resize handlers
  const handleMouseDown = (e, column) => {
    e.preventDefault();
    e.stopPropagation();

    setResizingColumn(column);
    resizeRef.current = {
      startX: e.clientX,
      startWidth: columnWidths[column] || 250,
      column: column
    };
  };

  useEffect(() => {
    if (!resizingColumn) return;

    const handleMouseMove = (e) => {
      const deltaX = e.clientX - resizeRef.current.startX;
      const newWidth = Math.max(80, resizeRef.current.startWidth + deltaX);

      setColumnWidths(prev => ({
        ...prev,
        [resizeRef.current.column]: newWidth
      }));
    };

    const handleMouseUp = () => {
      setResizingColumn(null);
    };

    document.addEventListener('mousemove', handleMouseMove);
    document.addEventListener('mouseup', handleMouseUp);

    document.body.style.userSelect = 'none';
    document.body.style.cursor = 'col-resize';

    return () => {
      document.removeEventListener('mousemove', handleMouseMove);
      document.removeEventListener('mouseup', handleMouseUp);
      document.body.style.userSelect = '';
      document.body.style.cursor = '';
    };
  }, [resizingColumn]);

  // Toggle row expansion
  const toggleRowExpansion = (rowIndex) => {
    setExpandedRows(prev => {
      const newSet = new Set(prev);
      if (newSet.has(rowIndex)) {
        newSet.delete(rowIndex);
      } else {
        newSet.add(rowIndex);
      }
      return newSet;
    });
  };

  if (!records || records.length === 0) {
    return <div className="text-jj-gray-06 text-center py-4 font-johnson-text">No records to display</div>;
  }

  // Derive columns (union of keys)
  const columns = Array.from(
    new Set(records.flatMap((r) => Object.keys(r)))
  );

  return (
    <>
      <div className="w-full overflow-x-auto border border-jj-gray-03 rounded-lg">
        <table className="min-w-full divide-y divide-jj-gray-03 bg-white" style={{ tableLayout: 'fixed' }}>
          <thead className="bg-jj-gray-01 sticky top-0 z-10">
            <tr>
              {/* Expand/Collapse column */}
              <th
                className="px-2 py-3 text-center text-xs font-johnson-text text-jj-gray-08 border-r border-jj-gray-03 font-normal"
                style={{
                  lineHeight: '120%',
                  width: '50px',
                  minWidth: '50px',
                  maxWidth: '50px'
                }}
              >
                <svg className="w-4 h-4 mx-auto" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 8V4m0 0h4M4 4l5 5m11-1V4m0 0h-4m4 0l-5 5M4 16v4m0 0h4m-4 0l5-5m11 5l-5-5m5 5v-4m0 4h-4" />
                </svg>
              </th>

              {columns.map((c) => {
                const width = columnWidths[c] || 250;
                return (
                  <th
                    key={c}
                    className="px-4 py-3 text-left text-xs font-johnson-text text-jj-gray-08 border-r border-jj-gray-03 last:border-r-0 whitespace-nowrap font-normal relative group"
                    style={{
                      lineHeight: '120%',
                      width: `${width}px`,
                      minWidth: `${width}px`,
                      maxWidth: `${width}px`
                    }}
                  >
                    <div className="flex items-center justify-between">
                      <span className="truncate">
                        {c.charAt(0).toUpperCase() + c.slice(1).toLowerCase()}
                      </span>
                    </div>

                    {/* Resize handle */}
                    <div
                      className="absolute top-0 right-0 w-1 h-full cursor-col-resize hover:bg-jj-blue-03 opacity-0 group-hover:opacity-100 transition-opacity"
                      onMouseDown={(e) => handleMouseDown(e, c)}
                      style={{
                        backgroundColor: resizingColumn === c ? '#0f68b2' : 'transparent',
                        opacity: resizingColumn === c ? 1 : undefined
                      }}
                    >
                      <div className="absolute top-1/2 right-0 w-1 h-8 -translate-y-1/2 bg-jj-blue-03" />
                    </div>
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody className="bg-white divide-y divide-jj-gray-02">
            {records.map((row, i) => {
              const rowKey = `row-${i}`;
              const isExpanded = expandedRows.has(i);

              return (
                <tr
                  key={rowKey}
                  className={`hover:bg-jj-gray-01 transition-colors ${i % 2 === 0 ? 'bg-white' : 'bg-gray-50'}`}
                >
                  {/* Expand/Collapse button */}
                  <td
                    className="px-2 py-3 text-center border-r border-jj-gray-02 cursor-pointer hover:bg-jj-blue-01 hover:bg-opacity-20 transition-colors"
                    onClick={() => toggleRowExpansion(i)}
                    style={{
                      width: '50px',
                      minWidth: '50px',
                      maxWidth: '50px'
                    }}
                  >
                    <button
                      className="text-jj-gray-06 hover:text-jj-blue-03 transition-colors p-1 rounded hover:bg-jj-blue-01"
                      title={isExpanded ? "Collapse row" : "Expand row"}
                    >
                      {isExpanded ? (
                        <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 15l7-7 7 7" />
                        </svg>
                      ) : (
                        <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                        </svg>
                      )}
                    </button>
                  </td>

                  {columns.map((col, j) => {
                    const val = row[col];
                    const cellValue = val == null ? "" : val;
                    const cellText = String(cellValue);
                    const hasMatch = searchedKeyword ? hasKeywordMatch(cellText, searchedKeyword) : false;
                    const cellKey = `${rowKey}-col-${j}-${col}`;
                    const width = columnWidths[col] || 250;

                    return (
                      <td
                        key={cellKey}
                        title={cellText}
                        onClick={() => setModal(cellText)}
                        className={`px-4 py-3 text-sm text-jj-gray-07 font-johnson-text border-r border-jj-gray-02 last:border-r-0 cursor-pointer transition-colors ${
                          hasMatch 
                            ? 'bg-yellow-50 hover:bg-yellow-100' 
                            : 'hover:bg-jj-blue-01 hover:bg-opacity-20'
                        }`}
                        style={{
                          width: `${width}px`,
                          minWidth: `${width}px`,
                          maxWidth: `${width}px`,
                          overflow: isExpanded ? 'visible' : 'hidden',
                          textOverflow: isExpanded ? 'clip' : 'ellipsis',
                          whiteSpace: isExpanded ? 'normal' : 'nowrap',
                          lineHeight: '120%',
                          wordWrap: isExpanded ? 'break-word' : 'normal'
                        }}
                      >
                        <span className={isExpanded ? "break-words" : "inline-block max-w-full overflow-hidden text-ellipsis"}>
                          {searchedKeyword ? highlightText(cellText, searchedKeyword) : cellText}
                        </span>
                      </td>
                    );
                  })}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {modal && (
        <div
          className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4"
          onClick={() => setModal(null)}
        >
          <div
            className="bg-white rounded-lg shadow-xl max-w-2xl w-full max-h-96 overflow-auto p-6"
            onClick={(e) => e.stopPropagation()}
          >
            <h3 className="text-lg font-johnson-display text-jj-gray-08 mb-3" style={{ lineHeight: '110%' }}>
              Cell content
            </h3>
            <div className="bg-jj-gray-01 p-4 rounded-md border border-jj-gray-03 text-sm text-jj-gray-07 font-johnson-text whitespace-pre-wrap break-words" style={{ lineHeight: '120%' }}>
              {searchedKeyword ? highlightText(modal, searchedKeyword) : modal}
            </div>
            <div className="mt-4 text-right">
              <button
                className="px-4 py-2 bg-jj-red text-white rounded-md hover:bg-jj-maroon-05 transition-colors font-johnson-text"
                onClick={() => setModal(null)}
              >
                Close
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}